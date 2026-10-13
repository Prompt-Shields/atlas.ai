"""Promptly enterprise catalogue: Stripe products, prices and the step meter.

This is the single declarative source for the Promptly side of Stripe
(Prompt-Shields/promptly-guide#45, #46, #47). ``scripts/stripe_catalog.py``
applies it to a Stripe account; ``enterprise.py`` looks prices up by the
lookup keys defined here.

Other services rely on the lookup keys, so treat them as a public contract.
A Stripe price's amount and interval cannot be edited, so changing an
amount here makes the sync create a new price, move the lookup key to it and
archive the old one. Existing subscriptions keep the old price until they
are moved.

This catalogue sits beside Atlas's self-serve Team plan, whose prices come
from ``STRIPE_PRICE_TEAM_MONTHLY`` / ``STRIPE_PRICE_TEAM_ANNUAL``. Nothing
here touches those.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.services.billing import stripe_client

log = structlog.get_logger()

CURRENCY = "eur"

# Lookup keys — a contract with other services. Do not rename.
WORK_CLIENT = "promptly_work_client"
ATLAS_ANNUAL = "promptly_atlas_annual"
LEARN_ANNUAL = "promptly_learn_annual"
ADOPT_ANNUAL = "promptly_adopt_annual"
SUITE_ANNUAL = "promptly_suite_annual"
PLATFORM_FEE_ANNUAL = "promptly_platform_fee_annual"
STEP_OVERAGE = "promptly_step_overage"

GUIDED_STEP_EVENT = "guided_step"

# Paid modules, by the short name callers use.
MODULE_PRICES: dict[str, str] = {
    "atlas": ATLAS_ANNUAL,
    "learn": LEARN_ANNUAL,
    "adopt": ADOPT_ANNUAL,
    "suite": SUITE_ANNUAL,
}

# Suite is Atlas.AI, Learn and Adopt together.
SUITE_COVERS = frozenset({"atlas", "learn", "adopt"})

# Metadata key that marks every object this catalogue owns.
OWNER_KEY = "promptly_catalog"


@dataclass(frozen=True)
class ProductSpec:
    # A custom Stripe product id, so the sync finds the product by id
    # rather than by an eventually consistent search.
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class PriceSpec:
    lookup_key: str
    product_id: str
    nickname: str
    unit_amount: int  # minor units (cents)
    interval: str  # "year" or "month"
    usage_type: str = "licensed"  # "licensed" or "metered"
    meter_event: str | None = None  # metered prices only
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MeterSpec:
    event_name: str
    display_name: str


PRODUCTS: tuple[ProductSpec, ...] = (
    ProductSpec(
        "promptly_work_client",
        "Promptly Work client",
        "The Promptly client for every employee. Free; quantity is provisioned users.",
    ),
    ProductSpec(
        "promptly_atlas",
        "Atlas.AI",
        "AI governance control tower, fed by the Work client and the browser extension.",
    ),
    ProductSpec(
        "promptly_learn",
        "Promptly Learn",
        "In-flow and portal learning, course generation, quizzes and Article 4 records.",
    ),
    ProductSpec(
        "promptly_adopt",
        "Promptly Adopt",
        "Walkthroughs, tips, nudges and automations for the customer's own apps.",
    ),
    ProductSpec(
        "promptly_suite",
        "Promptly Suite",
        "Atlas.AI, Learn and Adopt together.",
    ),
    ProductSpec(
        "promptly_platform_fee",
        "Promptly platform fee",
        "Onboarding, security review support and priority support. Once per customer.",
    ),
    ProductSpec(
        "promptly_guided_steps",
        "Promptly guided steps",
        "Guided steps above the pooled allowance.",
    ),
)

# All licensed prices are yearly so they can share one subscription: Stripe
# requires every item on a subscription to bill on the same interval.
PRICES: tuple[PriceSpec, ...] = (
    PriceSpec(WORK_CLIENT, "promptly_work_client", "Work client (free)", 0, "year"),
    # Placeholder: €5 a month from the business plan, pending a decision on
    # Atlas.AI's quoted price (Prompt-Shields/promptly-guide#53).
    PriceSpec(
        ATLAS_ANNUAL,
        "promptly_atlas",
        "Atlas.AI annual, per user",
        6000,
        "year",
        metadata={"placeholder": "pending promptly-guide#53"},
    ),
    PriceSpec(LEARN_ANNUAL, "promptly_learn", "Learn annual, per user", 2400, "year"),
    PriceSpec(ADOPT_ANNUAL, "promptly_adopt", "Adopt annual, per user", 4800, "year"),
    PriceSpec(SUITE_ANNUAL, "promptly_suite", "Suite annual, per user", 10800, "year"),
    PriceSpec(
        PLATFORM_FEE_ANNUAL,
        "promptly_platform_fee",
        "Platform fee, annual",
        1_000_000,
        "year",
    ),
    # Monthly in arrears, so it cannot sit on the yearly subscription above.
    PriceSpec(
        STEP_OVERAGE,
        "promptly_guided_steps",
        "Guided step overage",
        4,
        "month",
        usage_type="metered",
        meter_event=GUIDED_STEP_EVENT,
    ),
)

METERS: tuple[MeterSpec, ...] = (MeterSpec(GUIDED_STEP_EVENT, "Guided steps"),)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    verb: str  # "create", "update", "replace", "ok"
    kind: str  # "product", "price", "meter"
    key: str
    detail: str = ""

    def __str__(self) -> str:
        tail = f" ({self.detail})" if self.detail else ""
        return f"{self.verb:<8} {self.kind:<8} {self.key}{tail}"


def _price_params(spec: PriceSpec, meter_id: str | None) -> dict:
    recurring: dict = {"interval": spec.interval, "usage_type": spec.usage_type}
    if spec.usage_type == "metered":
        recurring["meter"] = meter_id
    return {
        "product": spec.product_id,
        "currency": CURRENCY,
        "unit_amount": spec.unit_amount,
        "recurring": recurring,
        "billing_scheme": "per_unit",
        # Business prices are quoted without VAT; Stripe Tax adds it.
        "tax_behavior": "exclusive",
        "lookup_key": spec.lookup_key,
        "nickname": spec.nickname,
        "metadata": {OWNER_KEY: "true", **spec.metadata},
    }


def _price_differs(price, spec: PriceSpec, meter_id: str | None) -> list[str]:
    """Return the immutable fields where ``price`` no longer matches ``spec``."""
    recurring = price.get("recurring") or {}
    product = price.get("product")
    product_id = product.get("id") if isinstance(product, dict) else product
    expected = {
        "product": spec.product_id,
        "currency": CURRENCY,
        "unit_amount": spec.unit_amount,
        "interval": spec.interval,
        "usage_type": spec.usage_type,
        "meter": meter_id if spec.usage_type == "metered" else None,
        "tax_behavior": "exclusive",
    }
    actual = {
        "product": product_id,
        "currency": price.get("currency"),
        "unit_amount": price.get("unit_amount"),
        "interval": recurring.get("interval"),
        "usage_type": recurring.get("usage_type"),
        "meter": recurring.get("meter"),
        "tax_behavior": price.get("tax_behavior"),
    }
    return [k for k in expected if expected[k] != actual[k]]


def _sync_product(spec: ProductSpec, *, dry_run: bool) -> Action:
    existing = stripe_client.retrieve_product(spec.id)
    wanted = {
        "name": spec.name,
        "description": spec.description,
        "active": True,
        "metadata": {OWNER_KEY: "true"},
    }
    if existing is None:
        if not dry_run:
            stripe_client.create_product(id=spec.id, **wanted)
        return Action("create", "product", spec.id, spec.name)

    changed = [k for k in ("name", "description", "active") if existing.get(k) != wanted[k]]
    if (existing.get("metadata") or {}).get(OWNER_KEY) != "true":
        changed.append("metadata")
    if not changed:
        return Action("ok", "product", spec.id)
    if not dry_run:
        stripe_client.modify_product(spec.id, **wanted)
    return Action("update", "product", spec.id, ", ".join(changed))


def _sync_meter(spec: MeterSpec, *, dry_run: bool) -> tuple[Action, str | None]:
    for meter in stripe_client.list_meters():
        if meter.get("event_name") == spec.event_name:
            if meter.get("status") != "active":
                if not dry_run:
                    stripe_client.reactivate_meter(meter["id"])
                return Action("update", "meter", spec.event_name, "reactivate"), meter["id"]
            return Action("ok", "meter", spec.event_name), meter["id"]

    meter_id = None
    if not dry_run:
        meter = stripe_client.create_meter(
            display_name=spec.display_name,
            event_name=spec.event_name,
            default_aggregation={"formula": "sum"},
            customer_mapping={"type": "by_id", "event_payload_key": "stripe_customer_id"},
            value_settings={"event_payload_key": "value"},
        )
        meter_id = meter["id"]
    return Action("create", "meter", spec.event_name, "sum per customer"), meter_id


def _sync_price(spec: PriceSpec, meter_id: str | None, *, dry_run: bool) -> Action:
    found = stripe_client.list_prices(lookup_keys=[spec.lookup_key], limit=1)
    existing = found[0] if found else None
    amount = f"{spec.unit_amount / 100:.2f} {CURRENCY.upper()}/{spec.interval}"

    if existing is None:
        if not dry_run:
            stripe_client.create_price(**_price_params(spec, meter_id))
        return Action("create", "price", spec.lookup_key, amount)

    if meter_id is None and spec.usage_type == "metered":
        # Dry run with no meter yet: the price must move to the new meter.
        diffs = ["meter"]
    else:
        diffs = _price_differs(existing, spec, meter_id)

    if diffs:
        # Amounts and intervals are immutable on a Stripe price: create a
        # replacement, move the lookup key to it, archive the old one.
        if not dry_run:
            params = _price_params(spec, meter_id)
            stripe_client.create_price(transfer_lookup_key=True, **params)
            stripe_client.modify_price(existing["id"], active=False)
        return Action("replace", "price", spec.lookup_key, f"{', '.join(diffs)} -> {amount}")

    wanted_meta = {OWNER_KEY: "true", **spec.metadata}
    current_meta = existing.get("metadata") or {}
    changed = []
    if not existing.get("active"):
        changed.append("active")
    if existing.get("nickname") != spec.nickname:
        changed.append("nickname")
    if any(current_meta.get(k) != v for k, v in wanted_meta.items()):
        changed.append("metadata")
    if not changed:
        return Action("ok", "price", spec.lookup_key)
    if not dry_run:
        stripe_client.modify_price(
            existing["id"], active=True, nickname=spec.nickname, metadata=wanted_meta
        )
    return Action("update", "price", spec.lookup_key, ", ".join(changed))


def sync_catalog(*, dry_run: bool = False) -> list[Action]:
    """Create or update every catalogue object; safe to run repeatedly.

    Products are found by their custom id, the meter by its event name and
    prices by lookup key, so a second run makes no changes. With
    ``dry_run`` nothing is written and the returned actions are the plan.
    """
    actions: list[Action] = []
    for product in PRODUCTS:
        actions.append(_sync_product(product, dry_run=dry_run))

    meter_ids: dict[str, str | None] = {}
    for meter in METERS:
        action, meter_id = _sync_meter(meter, dry_run=dry_run)
        actions.append(action)
        meter_ids[meter.event_name] = meter_id

    for price in PRICES:
        meter_id = meter_ids.get(price.meter_event) if price.meter_event else None
        actions.append(_sync_price(price, meter_id, dry_run=dry_run))

    for action in actions:
        log.info("stripe_catalog_action", action=str(action), dry_run=dry_run)
    return actions


def price_id(lookup_key: str) -> str:
    """Resolve a catalogue lookup key to the active Stripe price id."""
    found = stripe_client.list_prices(lookup_keys=[lookup_key], active=True, limit=1)
    if not found:
        raise LookupError(
            f"No active Stripe price with lookup_key {lookup_key!r}; "
            "run scripts/stripe_catalog.py against this account"
        )
    return found[0]["id"]

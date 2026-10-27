"""Promptly enterprise catalogue: Stripe products, prices and the step meter.

This is the single declarative source for the Promptly side of Stripe
(Prompt-Shields/promptly-guide#45, #46, #47). ``scripts/stripe_catalog.py``
applies it to a Stripe account; ``enterprise.py`` looks prices up by the
lookup keys defined here.

Other services rely on the lookup keys, so treat them as a public contract.

The amounts are not in this file. This repository is public and
CONTRIBUTING.md forbids committing pricing, so the amount of every price is
read at run time from a JSON file kept outside the repository (see
``load_amounts``). The code holds only the shape of the catalogue: products,
lookup keys, intervals, usage types and the meter.

A Stripe price's amount and interval cannot be edited, so changing an
amount in that file makes the sync create a new price, move the lookup key to
it and archive the old one. Existing subscriptions keep the old price until
they are moved. If a run stops between those two steps, the old price is left
active with no lookup key; the next run finds it and archives it.

This catalogue sits beside Atlas's self-serve Team plan, whose prices come
from ``STRIPE_PRICE_TEAM_MONTHLY`` / ``STRIPE_PRICE_TEAM_ANNUAL``. Nothing
here touches those.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

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
# Amounts come from the amounts file at run time; see ``load_amounts``.
PRICES: tuple[PriceSpec, ...] = (
    PriceSpec(WORK_CLIENT, "promptly_work_client", "Work client (free)", "year"),
    # Atlas.AI's quoted price is not settled (Prompt-Shields/promptly-guide#53).
    PriceSpec(
        ATLAS_ANNUAL,
        "promptly_atlas",
        "Atlas.AI annual, per user",
        "year",
        metadata={"placeholder": "pending promptly-guide#53"},
    ),
    PriceSpec(LEARN_ANNUAL, "promptly_learn", "Learn annual, per user", "year"),
    PriceSpec(ADOPT_ANNUAL, "promptly_adopt", "Adopt annual, per user", "year"),
    PriceSpec(SUITE_ANNUAL, "promptly_suite", "Suite annual, per user", "year"),
    PriceSpec(PLATFORM_FEE_ANNUAL, "promptly_platform_fee", "Platform fee, annual", "year"),
    # Monthly in arrears, so it cannot sit on the yearly subscription above.
    PriceSpec(
        STEP_OVERAGE,
        "promptly_guided_steps",
        "Guided step overage",
        "month",
        usage_type="metered",
        meter_event=GUIDED_STEP_EVENT,
    ),
)

METERS: tuple[MeterSpec, ...] = (MeterSpec(GUIDED_STEP_EVENT, "Guided steps"),)


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------


class AmountsError(ValueError):
    """The amounts file is missing, unreadable or does not match the catalogue."""


def validate_amounts(raw: object) -> dict[str, int]:
    """Check a ``{lookup_key: unit_amount}`` mapping against ``PRICES``.

    Every catalogue lookup key must be present, and no other. Each amount is
    a whole number of minor units (euro cents), zero or more, as Stripe's
    ``unit_amount`` requires. Floats, strings and booleans are refused rather
    than rounded, so a euro amount typed by mistake (``60`` for €0.60, or
    ``0.5``) is caught here and not billed.
    """
    if not isinstance(raw, Mapping):
        raise AmountsError("amounts must be a JSON object of lookup_key -> cents")
    wanted = {p.lookup_key for p in PRICES}
    missing = sorted(wanted - set(raw))
    extra = sorted(set(raw) - wanted)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unknown {', '.join(map(str, extra))}")
        raise AmountsError("amounts do not match the catalogue: " + "; ".join(parts))
    out: dict[str, int] = {}
    for key in sorted(wanted):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AmountsError(
                f"amount for {key} must be a whole number of cents >= 0, got {value!r}"
            )
        out[key] = value
    return out


def load_amounts(path: str | Path) -> dict[str, int]:
    """Read and validate the amounts file.

    The file is JSON, one entry per lookup key, amounts in euro cents::

        {"promptly_work_client": 0, "promptly_atlas_annual": 1234, ...}

    It is kept outside the repository (pricing must not be committed).
    """
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise AmountsError(f"amounts file not found: {path}") from None
    except (OSError, ValueError) as exc:
        raise AmountsError(f"cannot read amounts file {path}: {exc}") from None
    return validate_amounts(raw)


def format_amount(cents: int, interval: str) -> str:
    """``1234`` -> ``"12.34 EUR/year"``, in integer arithmetic."""
    return f"{cents // 100}.{cents % 100:02d} {CURRENCY.upper()}/{interval}"


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    verb: str  # "create", "update", "replace", "archive", "ok"
    kind: str  # "product", "price", "meter"
    key: str
    detail: str = ""

    def __str__(self) -> str:
        tail = f" ({self.detail})" if self.detail else ""
        return f"{self.verb:<8} {self.kind:<8} {self.key}{tail}"


def _price_params(spec: PriceSpec, amount: int, meter_id: str | None) -> dict:
    recurring: dict = {"interval": spec.interval, "usage_type": spec.usage_type}
    if spec.usage_type == "metered":
        recurring["meter"] = meter_id
    return {
        "product": spec.product_id,
        "currency": CURRENCY,
        "unit_amount": amount,
        "recurring": recurring,
        "billing_scheme": "per_unit",
        # Business prices are quoted without VAT; Stripe Tax adds it.
        "tax_behavior": "exclusive",
        "lookup_key": spec.lookup_key,
        "nickname": spec.nickname,
        "metadata": {OWNER_KEY: "true", **spec.metadata},
    }


def _price_differs(price, spec: PriceSpec, amount: int, meter_id: str | None) -> list[str]:
    """Return the immutable fields where ``price`` no longer matches ``spec``."""
    recurring = price.get("recurring") or {}
    product = price.get("product")
    product_id = product.get("id") if isinstance(product, dict) else product
    expected = {
        "product": spec.product_id,
        "currency": CURRENCY,
        "unit_amount": amount,
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


def _sync_price(spec: PriceSpec, amount: int, meter_id: str | None, *, dry_run: bool) -> Action:
    found = stripe_client.list_prices(lookup_keys=[spec.lookup_key], limit=1)
    existing = found[0] if found else None
    shown = format_amount(amount, spec.interval)

    if existing is None:
        if not dry_run:
            stripe_client.create_price(**_price_params(spec, amount, meter_id))
        return Action("create", "price", spec.lookup_key, shown)

    if meter_id is None and spec.usage_type == "metered":
        # Dry run with no meter yet: the price must move to the new meter.
        diffs = ["meter"]
    else:
        diffs = _price_differs(existing, spec, amount, meter_id)

    if diffs:
        # Amounts and intervals are immutable on a Stripe price: create a
        # replacement, move the lookup key to it, archive the old one.
        if not dry_run:
            params = _price_params(spec, amount, meter_id)
            stripe_client.create_price(transfer_lookup_key=True, **params)
            stripe_client.modify_price(existing["id"], active=False)
        return Action("replace", "price", spec.lookup_key, f"{', '.join(diffs)} -> {shown}")

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


def _archive_orphaned_prices(product_id: str, *, dry_run: bool) -> list[Action]:
    """Archive active catalogue prices on ``product_id`` that have no lookup key.

    Every price the sync creates has a lookup key, and a replacement takes the
    key from the price it replaces. So an active price of ours without one is
    the old half of a replacement whose run stopped before archiving it.
    Nothing resolves to it any more, but it is still offered in the dashboard
    and could be put on a subscription by hand. Prices without our metadata
    were made by someone else and are left alone.
    """
    actions = []
    for price in stripe_client.list_prices(product=product_id, active=True, limit=100):
        if price.get("lookup_key") or (price.get("metadata") or {}).get(OWNER_KEY) != "true":
            continue
        if not dry_run:
            stripe_client.modify_price(price["id"], active=False)
        actions.append(
            Action("archive", "price", price["id"], f"{product_id}, replaced, no lookup key")
        )
    return actions


def sync_catalog(amounts: Mapping[str, int], *, dry_run: bool = False) -> list[Action]:
    """Create or update every catalogue object; safe to run repeatedly.

    ``amounts`` maps each lookup key to its unit amount in cents, as returned
    by ``load_amounts``; it is validated again here. Products are found by
    their custom id, the meter by its event name and prices by lookup key, so
    a second run makes no changes. With ``dry_run`` nothing is written and
    the returned actions are the plan.

    A run that stopped half way through replacing a price is repaired: the
    old price, left active without its lookup key, is archived.
    """
    amounts = validate_amounts(amounts)
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
        actions.append(_sync_price(price, amounts[price.lookup_key], meter_id, dry_run=dry_run))

    # A product created in this run has no old prices to clean up.
    new_products = {a.key for a in actions if a.kind == "product" and a.verb == "create"}
    for product_id in dict.fromkeys(p.product_id for p in PRICES):
        if product_id not in new_products:
            actions.extend(_archive_orphaned_prices(product_id, dry_run=dry_run))

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

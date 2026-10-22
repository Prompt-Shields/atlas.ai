"""Unit tests for the Promptly catalogue sync (services/billing/catalog.py).

Stripe is replaced by an in-memory fake patched over the ``stripe_client``
wrappers, so the tests can check that a second run changes nothing.

The amounts here are made up. Real prices are never committed to this
repository; the script reads them from a file outside it.
"""

from __future__ import annotations

import itertools
import json

import pytest
import stripe

from app.services.billing import catalog, stripe_client

# Fixture amounts in cents. Deliberately not the real prices.
AMOUNTS: dict[str, int] = {
    catalog.WORK_CLIENT: 0,
    catalog.ATLAS_ANNUAL: 1111,
    catalog.LEARN_ANNUAL: 2222,
    catalog.ADOPT_ANNUAL: 3333,
    catalog.SUITE_ANNUAL: 4444,
    catalog.PLATFORM_FEE_ANNUAL: 555_555,
    catalog.STEP_OVERAGE: 7,
}


class FakeStripe:
    def __init__(self) -> None:
        self.products: dict[str, dict] = {}
        self.prices: list[dict] = []
        self.meters: list[dict] = []
        self.writes: list[str] = []
        self._ids = itertools.count(1)

    # products
    def retrieve_product(self, product_id):
        return self.products.get(product_id)

    def create_product(self, **kw):
        self.writes.append(f"create_product {kw['id']}")
        self.products[kw["id"]] = dict(kw)
        return self.products[kw["id"]]

    def modify_product(self, product_id, **kw):
        self.writes.append(f"modify_product {product_id}")
        self.products[product_id].update(kw)
        return self.products[product_id]

    # prices
    def list_prices(self, *, lookup_keys=None, product=None, active=None, limit=10):
        out = list(self.prices)
        if lookup_keys is not None:
            out = [p for p in out if p.get("lookup_key") in lookup_keys]
        if product is not None:
            out = [p for p in out if p["product"] == product]
        if active is not None:
            out = [p for p in out if p["active"] is active]
        return out[:limit]

    def create_price(self, *, transfer_lookup_key=False, **kw):
        self.writes.append(f"create_price {kw['lookup_key']}")
        if transfer_lookup_key:
            for p in self.prices:
                if p.get("lookup_key") == kw["lookup_key"]:
                    p["lookup_key"] = None
        price = {"id": f"price_{next(self._ids)}", "active": True, **kw}
        self.prices.append(price)
        return price

    def modify_price(self, price_id, **kw):
        self.writes.append(f"modify_price {price_id}")
        price = next(p for p in self.prices if p["id"] == price_id)
        price.update(kw)
        return price

    # meters
    def list_meters(self):
        return list(self.meters)

    def create_meter(self, **kw):
        self.writes.append(f"create_meter {kw['event_name']}")
        meter = {"id": f"mtr_{next(self._ids)}", "status": "active", **kw}
        self.meters.append(meter)
        return meter

    def reactivate_meter(self, meter_id):
        self.writes.append(f"reactivate_meter {meter_id}")
        meter = next(m for m in self.meters if m["id"] == meter_id)
        meter["status"] = "active"
        return meter


@pytest.fixture
def fake(monkeypatch) -> FakeStripe:
    f = FakeStripe()
    for name in (
        "retrieve_product",
        "create_product",
        "modify_product",
        "list_prices",
        "create_price",
        "modify_price",
        "list_meters",
        "create_meter",
        "reactivate_meter",
    ):
        monkeypatch.setattr(stripe_client, name, getattr(f, name))
    return f


def _by_key(fake: FakeStripe, key: str) -> dict:
    return next(p for p in fake.prices if p.get("lookup_key") == key)


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------


def test_lookup_keys_are_the_agreed_contract():
    assert {p.lookup_key for p in catalog.PRICES} == {
        "promptly_work_client",
        "promptly_atlas_annual",
        "promptly_learn_annual",
        "promptly_adopt_annual",
        "promptly_suite_annual",
        "promptly_platform_fee_annual",
        "promptly_step_overage",
    }


def test_no_amounts_in_the_code():
    """Pricing must not be committed (CONTRIBUTING.md); specs carry no amount."""
    assert all(not hasattr(p, "unit_amount") for p in catalog.PRICES)


# ---------------------------------------------------------------------------
# Amounts file
# ---------------------------------------------------------------------------


def test_validate_amounts_accepts_whole_cents():
    assert catalog.validate_amounts(AMOUNTS) == AMOUNTS


@pytest.mark.parametrize("bad", [12.5, 12.0, "1200", True, -1, None])
def test_validate_amounts_refuses_non_cents(bad):
    with pytest.raises(catalog.AmountsError, match=catalog.ATLAS_ANNUAL):
        catalog.validate_amounts({**AMOUNTS, catalog.ATLAS_ANNUAL: bad})


def test_validate_amounts_refuses_missing_and_unknown_keys():
    partial = {k: v for k, v in AMOUNTS.items() if k != catalog.SUITE_ANNUAL}
    with pytest.raises(catalog.AmountsError, match="missing promptly_suite_annual"):
        catalog.validate_amounts(partial)
    with pytest.raises(catalog.AmountsError, match="unknown promptly_typo"):
        catalog.validate_amounts({**AMOUNTS, "promptly_typo": 1})
    with pytest.raises(catalog.AmountsError, match="JSON object"):
        catalog.validate_amounts([1, 2])


def test_load_amounts_reads_json(tmp_path):
    path = tmp_path / "amounts.json"
    path.write_text(json.dumps(AMOUNTS))
    assert catalog.load_amounts(path) == AMOUNTS


def test_load_amounts_reports_missing_and_malformed_files(tmp_path):
    with pytest.raises(catalog.AmountsError, match="not found"):
        catalog.load_amounts(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(catalog.AmountsError, match="cannot read"):
        catalog.load_amounts(bad)


@pytest.mark.parametrize(
    ("cents", "shown"),
    [(0, "0.00 EUR/year"), (7, "0.07 EUR/year"), (1234, "12.34 EUR/year"), (100, "1.00 EUR/year")],
)
def test_format_amount_uses_integer_cents(cents, shown):
    assert catalog.format_amount(cents, "year") == shown


def test_subscription_prices_share_the_yearly_interval():
    """Stripe requires one interval per subscription; only overage is monthly."""
    for p in catalog.PRICES:
        if p.lookup_key == catalog.STEP_OVERAGE:
            assert (p.interval, p.usage_type, p.meter_event) == ("month", "metered", "guided_step")
        else:
            assert (p.interval, p.usage_type) == ("year", "licensed")


def test_every_price_has_a_product():
    product_ids = {p.id for p in catalog.PRODUCTS}
    assert all(p.product_id in product_ids for p in catalog.PRICES)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_first_run_creates_everything(fake):
    actions = catalog.sync_catalog(AMOUNTS)
    assert {a.verb for a in actions} == {"create"}
    assert set(fake.products) == {p.id for p in catalog.PRODUCTS}
    assert len(fake.prices) == len(catalog.PRICES)
    assert len(fake.meters) == 1


def test_meter_sums_guided_steps_per_customer(fake):
    catalog.sync_catalog(AMOUNTS)
    meter = fake.meters[0]
    assert meter["event_name"] == "guided_step"
    assert meter["default_aggregation"] == {"formula": "sum"}
    assert meter["customer_mapping"]["type"] == "by_id"


def test_created_prices_have_expected_shape(fake):
    catalog.sync_catalog(AMOUNTS)
    for key, cents in AMOUNTS.items():
        assert _by_key(fake, key)["unit_amount"] == cents
    work = _by_key(fake, catalog.WORK_CLIENT)
    assert work["currency"] == "eur"
    assert work["recurring"] == {"interval": "year", "usage_type": "licensed"}
    assert work["tax_behavior"] == "exclusive"

    overage = _by_key(fake, catalog.STEP_OVERAGE)
    assert overage["recurring"]["usage_type"] == "metered"
    assert overage["recurring"]["meter"] == fake.meters[0]["id"]

    atlas = _by_key(fake, catalog.ATLAS_ANNUAL)
    assert "53" in atlas["metadata"]["placeholder"]


def test_second_run_changes_nothing(fake):
    catalog.sync_catalog(AMOUNTS)
    fake.writes.clear()
    actions = catalog.sync_catalog(AMOUNTS)
    assert {a.verb for a in actions} == {"ok"}
    assert fake.writes == []


def test_dry_run_writes_nothing(fake):
    actions = catalog.sync_catalog(AMOUNTS, dry_run=True)
    assert fake.writes == []
    assert {a.verb for a in actions} == {"create"}
    assert len(actions) == len(catalog.PRODUCTS) + len(catalog.METERS) + len(catalog.PRICES)


def test_changed_amount_replaces_price_and_moves_lookup_key(fake):
    catalog.sync_catalog(AMOUNTS)
    old = _by_key(fake, catalog.ATLAS_ANNUAL)

    actions = catalog.sync_catalog({**AMOUNTS, catalog.ATLAS_ANNUAL: 9999})
    replaced = [a for a in actions if a.verb == "replace"]
    assert [a.key for a in replaced] == [catalog.ATLAS_ANNUAL]

    current = _by_key(fake, catalog.ATLAS_ANNUAL)
    assert current["unit_amount"] == 9999
    assert current["id"] != old["id"]
    assert old["active"] is False
    assert old["lookup_key"] is None


def test_interrupted_replace_is_repaired_on_next_run(fake, monkeypatch):
    """A run that stops after creating the replacement leaves the old price live."""
    catalog.sync_catalog(AMOUNTS)
    old = _by_key(fake, catalog.ATLAS_ANNUAL)
    new_amounts = {**AMOUNTS, catalog.ATLAS_ANNUAL: 9999}

    real_modify = fake.modify_price

    def failing_modify(price_id, **kw):
        if price_id == old["id"] and kw.get("active") is False:
            raise stripe.APIConnectionError("network down")
        return real_modify(price_id, **kw)

    monkeypatch.setattr(stripe_client, "modify_price", failing_modify)
    with pytest.raises(stripe.APIConnectionError):
        catalog.sync_catalog(new_amounts)
    # The broken state: the key moved, but the old price is still active.
    assert old["lookup_key"] is None
    assert old["active"] is True

    monkeypatch.setattr(stripe_client, "modify_price", real_modify)
    fake.writes.clear()
    actions = catalog.sync_catalog(new_amounts)

    archived = [a for a in actions if a.verb == "archive"]
    assert [a.key for a in archived] == [old["id"]]
    assert old["active"] is False
    assert fake.writes == [f"modify_price {old['id']}"]
    assert _by_key(fake, catalog.ATLAS_ANNUAL)["unit_amount"] == 9999

    # Repaired: a further run changes nothing.
    fake.writes.clear()
    assert {a.verb for a in catalog.sync_catalog(new_amounts)} == {"ok"}
    assert fake.writes == []


def test_repair_dry_run_reports_without_archiving(fake):
    catalog.sync_catalog(AMOUNTS)
    old = _by_key(fake, catalog.LEARN_ANNUAL)
    old["lookup_key"] = None  # as left by an interrupted replace
    spec = next(p for p in catalog.PRICES if p.lookup_key == catalog.LEARN_ANNUAL)
    fake.create_price(**catalog._price_params(spec, AMOUNTS[catalog.LEARN_ANNUAL], None))
    fake.writes.clear()

    actions = catalog.sync_catalog(AMOUNTS, dry_run=True)
    assert [(a.verb, a.key) for a in actions if a.verb == "archive"] == [("archive", old["id"])]
    assert old["active"] is True
    assert fake.writes == []


def test_repair_leaves_prices_it_does_not_own(fake):
    catalog.sync_catalog(AMOUNTS)
    fake.prices.append(
        {"id": "price_manual", "product": "promptly_learn", "active": True, "metadata": {}}
    )
    fake.writes.clear()
    actions = catalog.sync_catalog(AMOUNTS)
    assert {a.verb for a in actions} == {"ok"}
    assert fake.writes == []


def test_archived_price_and_product_are_reactivated(fake):
    catalog.sync_catalog(AMOUNTS)
    _by_key(fake, catalog.LEARN_ANNUAL)["active"] = False
    fake.products["promptly_learn"]["active"] = False

    actions = {(a.kind, a.key): a for a in catalog.sync_catalog(AMOUNTS)}
    assert actions[("price", catalog.LEARN_ANNUAL)].verb == "update"
    assert actions[("product", "promptly_learn")].verb == "update"
    assert _by_key(fake, catalog.LEARN_ANNUAL)["active"] is True
    assert fake.products["promptly_learn"]["active"] is True
    assert len(fake.prices) == len(catalog.PRICES)


def test_inactive_meter_is_reactivated_not_duplicated(fake):
    catalog.sync_catalog(AMOUNTS)
    fake.meters[0]["status"] = "inactive"
    catalog.sync_catalog(AMOUNTS)
    assert len(fake.meters) == 1
    assert fake.meters[0]["status"] == "active"


def test_price_id_resolves_active_price(fake):
    catalog.sync_catalog(AMOUNTS)
    assert catalog.price_id(catalog.SUITE_ANNUAL) == _by_key(fake, catalog.SUITE_ANNUAL)["id"]


def test_price_id_raises_when_catalogue_missing(fake):
    with pytest.raises(LookupError, match="stripe_catalog"):
        catalog.price_id(catalog.SUITE_ANNUAL)


# ---------------------------------------------------------------------------
# stripe_client wrappers used by the catalogue
# ---------------------------------------------------------------------------


def test_retrieve_product_returns_none_when_missing(monkeypatch):
    def missing(product_id):
        raise stripe.InvalidRequestError("No such product", "id", code="resource_missing")

    monkeypatch.setattr(stripe.Product, "retrieve", missing)
    assert stripe_client.retrieve_product("promptly_atlas") is None


def test_retrieve_product_reraises_other_errors(monkeypatch):
    def bad(product_id):
        raise stripe.InvalidRequestError("bad", "id", code="parameter_invalid")

    monkeypatch.setattr(stripe.Product, "retrieve", bad)
    with pytest.raises(stripe.InvalidRequestError):
        stripe_client.retrieve_product("promptly_atlas")


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------


@pytest.fixture
def amounts_file(tmp_path) -> str:
    path = tmp_path / "catalog-amounts.json"
    path.write_text(json.dumps(AMOUNTS))
    return str(path)


def test_script_refuses_live_key(monkeypatch, capsys, amounts_file):
    from scripts import stripe_catalog

    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_secret_key", "sk_live_x")
    assert stripe_catalog.main(["--amounts", amounts_file]) == 2
    assert "--live" in capsys.readouterr().err


def test_script_refuses_to_run_without_amounts(monkeypatch, fake, capsys):
    from scripts import stripe_catalog

    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_catalog_amounts_file", "")
    assert stripe_catalog.main([]) == 2
    assert "amounts" in capsys.readouterr().err
    assert fake.writes == []


def test_script_refuses_invalid_amounts(monkeypatch, fake, capsys, tmp_path):
    from scripts import stripe_catalog

    bad = tmp_path / "amounts.json"
    bad.write_text(json.dumps({**AMOUNTS, catalog.STEP_OVERAGE: 0.04}))
    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_secret_key", "sk_test_x")
    assert stripe_catalog.main(["--amounts", str(bad)]) == 2
    assert catalog.STEP_OVERAGE in capsys.readouterr().err
    assert fake.writes == []


def test_script_reads_amounts_file_from_settings(monkeypatch, fake, capsys, amounts_file):
    from scripts import stripe_catalog

    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_catalog_amounts_file", amounts_file)
    assert stripe_catalog.main([]) == 0
    assert _by_key(fake, catalog.SUITE_ANNUAL)["unit_amount"] == AMOUNTS[catalog.SUITE_ANNUAL]


def test_script_dry_run_without_key_prints_plan(monkeypatch, capsys, amounts_file):
    from scripts import stripe_catalog

    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_secret_key", "")
    assert stripe_catalog.main(["--dry-run", "--amounts", amounts_file]) == 0
    out = capsys.readouterr().out
    for p in catalog.PRICES:
        assert p.lookup_key in out
    assert "44.44 EUR/year" in out


def test_script_runs_sync_with_test_key(monkeypatch, fake, capsys, amounts_file):
    from scripts import stripe_catalog

    monkeypatch.setattr(stripe_catalog.get_settings(), "stripe_secret_key", "sk_test_x")
    assert stripe_catalog.main(["--dry-run", "--amounts", amounts_file]) == 0
    assert "test mode (dry run" in capsys.readouterr().out
    assert fake.writes == []

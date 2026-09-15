"""Token price book for derived self-hosted cost.

A pushed usage record carries tokens, not dollars — the dollars are on the
customer's own Azure/AWS bill. Cost is therefore *derived*, and everything it
produces is tagged ``cost_source=derived_tokens`` so the dashboard can badge it
as an estimate rather than billed spend. Never let a derived figure masquerade
as an invoice line.

Prices are USD per **one million** tokens, which is how every vendor publishes
them, split input/output. Two layers:

  1. A per-integration override in ``Integration.config_json`` under
     ``price_book``, so a customer with negotiated or regional pricing is not
     stuck with our list prices. This is the design doc's stated v1 approach —
     a dedicated price-book table is explicitly out of scope (YAGNI).
  2. The built-in defaults below.

An unpriced model is **not** silently costed at zero. Zero is indistinguishable
from "this model is free" on a dashboard, and it under-reports spend, which is
the failure a cost tool exists to prevent. The record is accepted, counted, and
its tokens are recorded, but it is reported back as unpriced so the caller can
fix the price book — see ``price_for``.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from app.models.integration import Integration


class ModelPrice(NamedTuple):
    """USD per one million tokens."""

    input_per_mtok: Decimal
    output_per_mtok: Decimal


def _p(input_usd: str, output_usd: str) -> ModelPrice:
    return ModelPrice(Decimal(input_usd), Decimal(output_usd))


# Built-in list prices, USD per 1M tokens. Deliberately a small, boring table
# of the models customers actually self-host behind Foundry/Bedrock. It will go
# stale — that is what the per-integration override is for, and why an unpriced
# model is surfaced rather than guessed at.
DEFAULT_PRICE_BOOK: dict[str, ModelPrice] = {
    # Anthropic on Bedrock / Foundry
    "claude-opus-4": _p("15.00", "75.00"),
    "claude-sonnet-4": _p("3.00", "15.00"),
    "claude-haiku-3-5": _p("0.80", "4.00"),
    # OpenAI on Azure AI Foundry
    "gpt-4o": _p("2.50", "10.00"),
    "gpt-4o-mini": _p("0.15", "0.60"),
    "gpt-4-turbo": _p("10.00", "30.00"),
    # Meta / Mistral, commonly self-hosted on Bedrock
    "llama-3-70b": _p("2.65", "3.50"),
    "llama-3-8b": _p("0.30", "0.60"),
    "mistral-large": _p("4.00", "12.00"),
}


# Trailing markers that are packaging, not identity.
_REVISION_SUFFIX = re.compile(r"-v\d+$")
_ISO_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_SUFFIX = re.compile(r"-\d{8}$")


def normalise_model(name: str) -> str:
    """Fold a vendor's deployment id onto a price-book key.

    Deployment names arrive noisy — `gpt-4o-2024-08-06`, an Azure deployment
    alias, a Bedrock id like `anthropic.claude-sonnet-4-v1:0`. Lower-case, drop
    the vendor prefix and revision marker, and strip a release date.

    Only *version and date* suffixes are stripped, never a bare number. A
    trailing digit is usually model identity — `claude-sonnet-4`, `llama-3-8b`
    — and stripping it maps a real model onto a key that is not in the book,
    which prices it at nothing and under-reports the customer's bill. The
    invariant that every built-in key normalises to itself is asserted in
    tests/unit/test_self_hosted_cost_ingest.py.
    """
    key = name.strip().lower()
    # Bedrock model ids are `vendor.model-version:revision`.
    if ":" in key:
        key = key.split(":", 1)[0]
    if "." in key:
        key = key.rsplit(".", 1)[-1]
    key = key.replace("_", "-").replace(".", "-")

    # Order matters: `claude-3-5-sonnet-20241022-v2` carries both markers.
    for pattern in (_REVISION_SUFFIX, _ISO_DATE_SUFFIX, _COMPACT_DATE_SUFFIX):
        key = pattern.sub("", key)
    return key


def load_price_book(integration: Integration | None) -> dict[str, ModelPrice]:
    """Defaults overlaid with any per-integration override.

    The override is `{"price_book": {"<model>": {"input_per_mtok": "1.23",
    "output_per_mtok": "4.56"}}}` in `config_json`. A malformed entry is
    skipped rather than failing the whole ingest — one bad price should not
    stop a customer reporting spend.
    """
    book = dict(DEFAULT_PRICE_BOOK)
    if integration is None or not integration.config_json:
        return book
    try:
        config = json.loads(integration.config_json)
    except json.JSONDecodeError:
        return book
    if not isinstance(config, dict):
        return book

    overrides = config.get("price_book")
    if not isinstance(overrides, dict):
        return book

    for model, price in overrides.items():
        if not isinstance(price, dict):
            continue
        try:
            book[normalise_model(str(model))] = ModelPrice(
                Decimal(str(price["input_per_mtok"])),
                Decimal(str(price["output_per_mtok"])),
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return book


def price_for(model: str, book: dict[str, ModelPrice]) -> ModelPrice | None:
    """The price for a model, or None when it is unpriced.

    None is deliberate. Returning a zero price would put a $0.00 row on a spend
    dashboard, which reads as "this model costs nothing" rather than "we do not
    know what this costs" — and quietly under-reports the customer's true bill.
    """
    return book.get(normalise_model(model))


def derive_cost_usd(
    *,
    tokens_in: int,
    tokens_out: int,
    price: ModelPrice,
) -> Decimal:
    """Token counts multiplied by price, in USD.

    Kept at full Decimal precision; the ledger column is Numeric(14, 6) and
    rounds on write. Rounding here instead would lose fractions of a cent on
    every call, which over millions of calls is a real number.
    """
    million = Decimal(1_000_000)
    return (
        Decimal(tokens_in) * price.input_per_mtok + Decimal(tokens_out) * price.output_per_mtok
    ) / million

"""Unit tests for app.services.oauth_callback_html.

The callback page puts a JSON payload inside an inline `<script>`. Some
payload values come from outside (a Slack team name, a provider error), so
these tests check that such a value cannot end the script early and that
the JSON the browser parses is still the payload we built.
"""

from __future__ import annotations

import json
import re

import pytest

from app.services.oauth_callback_html import (
    oauth_error_response,
    oauth_success_response,
)

pytestmark = [pytest.mark.unit]

_BREAKOUT = "</script><script>alert(1)</script>"
_PAYLOAD_RE = re.compile(r"var payload = (.*);\n")


def _body(response) -> str:
    return response.body.decode("utf-8")


def _script(html: str) -> str:
    """The text between the page's only <script> and its closing tag."""
    start = html.index("<script>") + len("<script>")
    end = html.index("</script>", start)
    return html[start:end]


def _payload(html: str) -> dict:
    match = _PAYLOAD_RE.search(_script(html))
    assert match, "payload assignment not found inside the script"
    return json.loads(match.group(1))


def test_success_response_renders() -> None:
    response = oauth_success_response(
        provider="slack",
        display_name="Acme",
        extra_data={"team_id": "T123"},
    )
    html = _body(response)

    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert "Acme connected" in html
    assert _payload(html) == {
        "source": "atlas-oauth",
        "status": "connected",
        "provider": "slack",
        "display_name": "Acme",
        "team_id": "T123",
    }


def test_error_response_renders() -> None:
    response = oauth_error_response(provider="microsoft", error_message="denied")
    html = _body(response)

    assert response.status_code == 200
    assert response.media_type == "text/html"
    assert "Couldn't connect microsoft" in html
    assert _payload(html) == {
        "source": "atlas-oauth",
        "status": "error",
        "provider": "microsoft",
        "error": "denied",
    }


def test_target_origin_is_unchanged() -> None:
    # Narrowing the postMessage target is a separate behaviour change.
    for response in (
        oauth_success_response(provider="slack", display_name="Acme"),
        oauth_error_response(provider="slack", error_message="x"),
    ):
        assert "postMessage(payload, '*')" in _body(response)


@pytest.mark.parametrize(
    "response",
    [
        oauth_success_response(provider="slack", display_name=_BREAKOUT),
        oauth_success_response(
            provider="slack",
            display_name="Acme",
            extra_data={"team_name": _BREAKOUT},
        ),
        oauth_error_response(provider="slack", error_message=_BREAKOUT),
        oauth_error_response(provider=_BREAKOUT, error_message="x"),
    ],
    ids=["display_name", "extra_data", "error_message", "provider"],
)
def test_payload_cannot_break_out_of_script(response) -> None:
    html = _body(response)

    # Exactly one script element, and the hostile markup never appears raw.
    assert html.count("<script>") == 1
    assert html.count("</script>") == 1
    assert _BREAKOUT not in html
    # The JSON literal itself carries no raw angle brackets or ampersands.
    literal = _PAYLOAD_RE.search(_script(html)).group(1)
    assert not set(literal) & {"<", ">", "&"}


@pytest.mark.parametrize(
    "value",
    [
        _BREAKOUT,
        "<!-- comment -->",
        "Tom & Jerry's <Team>",
        "line sep para",
        "ünïcödé — ✓",
        'quote " and backslash \\',
    ],
)
def test_payload_round_trips(value: str) -> None:
    success = oauth_success_response(
        provider="slack", display_name=value, extra_data={"team_name": value}
    )
    error = oauth_error_response(provider="slack", error_message=value)

    success_payload = _payload(_body(success))
    assert success_payload["display_name"] == value
    assert success_payload["team_name"] == value
    assert _payload(_body(error))["error"] == value


def test_raw_line_separators_never_reach_the_script() -> None:
    html = _body(oauth_success_response(provider="slack", display_name="a b c"))
    script = _script(html)

    assert " " not in script
    assert " " not in script

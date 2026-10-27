"""HTML response builder for OAuth callback success.

Microsoft and Slack OAuth callbacks both end in the same UX flow:

  1. User approves consent in the provider's window (opened via
     window.open from the Integrations page).
  2. Provider redirects to our callback endpoint with `?code=…`.
  3. The callback exchanges the code, persists the encrypted token,
     and now needs to:
       a. Notify the parent atlas window so it refreshes its
          integration list.
       b. Close itself.

This helper produces the tiny HTML page that does (a) + (b). The
message shape is `{ source: 'atlas-oauth', provider: '<...>',
status: 'connected' | 'error' }`. The Integrations page listens
for it and re-fetches /api/v1/integrations.

The HTML is intentionally minimal — no Tailwind, no React, just an
inline `<script>` and a friendly fallback message for when
`window.opener` is null (popup-was-redirected-to-tab cases).
"""

from __future__ import annotations

import json
from html import escape

from fastapi.responses import HTMLResponse

# Characters that let a JSON string end the inline <script> early or open an
# HTML comment there, mapped to JS escapes that parse to the same characters.
# U+2028 and U+2029 are line terminators in older JS engines, so a raw one
# inside a string literal is a syntax error.
_SCRIPT_SAFE = str.maketrans(
    {
        "<": "\\u003c",
        ">": "\\u003e",
        "&": "\\u0026",
        "\u2028": "\\u2028",
        "\u2029": "\\u2029",
    }
)


def _script_json(payload: dict[str, str]) -> str:
    """Serialise `payload` as a JS literal that is safe inside `<script>`.

    Values such as a Slack team name or a provider error come from outside,
    so `</script>` in one must not end the script. The escapes only appear
    inside JSON strings, so the parsed value is unchanged.
    """
    return json.dumps(payload).translate(_SCRIPT_SAFE)


def oauth_success_response(
    *,
    provider: str,
    display_name: str,
    extra_data: dict[str, str] | None = None,
) -> HTMLResponse:
    """Build the success HTML.

    `extra_data` is shallow-merged into the postMessage payload so
    callers can include provider-specific bits (team_id for Slack,
    aad_tenant for Microsoft).
    """
    payload = {
        "source": "atlas-oauth",
        "status": "connected",
        "provider": provider,
        "display_name": display_name,
    }
    if extra_data:
        payload.update(extra_data)

    payload_json = _script_json(payload)
    safe_display = escape(display_name)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Connected — atlas</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f9fafb;
    color: #111827;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    margin: 0;
  }}
  .card {{
    background: #fff;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 28px 32px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    text-align: center;
    max-width: 360px;
  }}
  .check {{
    color: #10b981;
    font-size: 32px;
    line-height: 1;
  }}
  h1 {{ font-size: 16px; margin: 12px 0 4px; }}
  p {{ font-size: 13px; color: #6b7280; margin: 6px 0; }}
  button {{
    margin-top: 14px;
    background: #04AA6D;
    color: #fff;
    border: 0;
    padding: 8px 16px;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="check">✓</div>
    <h1>{safe_display} connected</h1>
    <p>You can close this window — atlas will refresh automatically.</p>
    <button type="button" onclick="window.close()">Close</button>
  </div>
  <script>
    (function () {{
      var payload = {payload_json};
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, '*');
        }}
      }} catch (e) {{
        // Cross-origin error — ignore; parent will refresh on focus.
      }}
      // Best-effort close after a short delay so the message lands.
      setTimeout(function () {{
        try {{ window.close(); }} catch (e) {{}}
      }}, 600);
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)


def oauth_error_response(*, provider: str, error_message: str) -> HTMLResponse:
    """Same shape, error variant."""
    payload = {
        "source": "atlas-oauth",
        "status": "error",
        "provider": provider,
        "error": error_message,
    }
    payload_json = _script_json(payload)
    safe_err = escape(error_message)
    safe_provider = escape(provider)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Connection failed — atlas</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f9fafb; color: #111827;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; margin: 0;
  }}
  .card {{
    background: #fff; border: 1px solid #fecaca; border-left: 4px solid #ef4444;
    border-radius: 12px; padding: 24px 28px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    max-width: 440px;
  }}
  h1 {{ font-size: 16px; margin: 0 0 6px; color: #b91c1c; }}
  p {{ font-size: 13px; color: #6b7280; margin: 6px 0; }}
  code {{ font-size: 12px; background: #fef2f2; padding: 2px 4px; border-radius: 3px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Couldn't connect {safe_provider}</h1>
    <p><code>{safe_err}</code></p>
    <p>You can close this window and retry from the Integrations tab.</p>
  </div>
  <script>
    (function () {{
      var payload = {payload_json};
      try {{
        if (window.opener && !window.opener.closed) {{
          window.opener.postMessage(payload, '*');
        }}
      }} catch (e) {{}}
    }})();
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200)

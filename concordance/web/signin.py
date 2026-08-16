"""The page shown to someone who has not signed in yet.

Loading a protected server used to answer the browser's document request with
``{"error": "This server requires an access token."}`` -- correct for the API,
and a wall of JSON where a sign-in form belonged. This is that page.

Server-rendered, and self-contained for the same reason the rest of the
interface is: one file, no external asset, no build step. It cannot be part of
the React bundle, because the bundle is only reachable *after* you are signed
in -- the credential is checked before any page is served.

Two credentials, and the page shows whichever the server was started with:

  * **Auth0**, which brings login, signup and Google with it. None of those
    are screens built here; Universal Login renders them, and `screen_hint`
    picks which one it opens on. The SDK is loaded as a module from the
    tenant's own CDN only when a tenant is configured, so the offline case
    never reaches for the network.
  * **A personal token**, pasted. Honest about what it is -- a bearer token,
    not an account -- because a form that looks like a password login implies
    a reset flow and a session lifetime that do not exist.
"""

from __future__ import annotations

import html
import json


def _wants_html(accept: str) -> bool:
    """Whether this request is a browser asking for a page.

    `fetch()` from the interface sends `Accept: application/json`; a browser
    address bar sends `text/html`. Getting this wrong in either direction is
    bad -- JSON in the address bar, or an HTML page arriving where the client
    expected a parseable error.
    """
    return "text/html" in accept.lower()


def sign_in_page(*, model: str, auth0=None, accepts_token: bool) -> bytes:
    """Render the page. Depends only on how the server was started."""
    safe_model = html.escape(model or "this model")

    if auth0 is not None:
        config = json.dumps(
            {
                "domain": auth0.domain,
                "clientId": getattr(auth0, "client_id", ""),
                "audience": auth0.audience,
            }
        )
        methods = _AUTH0_BLOCK
        script = _AUTH0_SCRIPT.replace("__CONFIG__", config)
    else:
        config, methods, script = "null", "", ""

    if accepts_token:
        methods += _TOKEN_BLOCK if not auth0 else _TOKEN_BLOCK_SECONDARY

    return (
        _PAGE.replace("__MODEL__", safe_model)
        .replace("__METHODS__", methods)
        .replace("__SCRIPT__", script)
        .encode("utf-8")
    )


_AUTH0_BLOCK = """
      <button id="login" class="primary" type="button">Sign in</button>
      <button id="signup" class="secondary" type="button">Create an account</button>
      <p class="note">
        Sign-in, account creation and Google are handled by Auth0. You will be
        taken to your organisation's sign-in page and returned here.
      </p>
      <p id="problem" class="problem" hidden></p>
"""

_TOKEN_BLOCK = """
      <form method="GET" action="/">
        <label for="token">Your personal access token</label>
        <input id="token" name="token" type="password" autocomplete="off"
               autocapitalize="off" spellcheck="false" required
               placeholder="Paste the token you were given" />
        <button class="primary" type="submit">Sign in</button>
      </form>
      <p class="note">
        This is a bearer token, not an account: there is no password to reset
        and no session to expire. Whoever holds it is treated as you, so it
        belongs somewhere only you can read.
      </p>
"""

_TOKEN_BLOCK_SECONDARY = """
      <details>
        <summary>Sign in with a personal token instead</summary>
        <form method="GET" action="/">
          <label for="token">Your personal access token</label>
          <input id="token" name="token" type="password" autocomplete="off"
                 autocapitalize="off" spellcheck="false" required
                 placeholder="Paste the token you were given" />
          <button class="secondary" type="submit">Use token</button>
        </form>
        <p class="note">
          Kept for machines that cannot reach Auth0 -- an offline or air-gapped
          install still has a way in.
        </p>
      </details>
"""

#: Loaded from the tenant's own CDN, and only when a tenant is configured. The
#: rest of this project ships no external request at all; this one is
#: unavoidable, because the SDK has to come from somewhere and bundling it
#: would put an Auth0 dependency into every offline install that will never
#: use it.
_AUTH0_SCRIPT = """
  <script type="module">
    const config = __CONFIG__;
    const problem = document.getElementById("problem");
    const show = (message) => {
      problem.textContent = message;
      problem.hidden = false;
    };

    if (!config.clientId) {
      show(
        "This server was started with an Auth0 domain but no client id, so " +
        "sign-in cannot start. Pass --auth0-client-id."
      );
    } else {
      try {
        const { createAuth0Client } = await import(
          "https://cdn.auth0.com/js/auth0-spa-js/2.1/auth0-spa-js.production.esm.js"
        );
        const auth0 = await createAuth0Client({
          domain: config.domain,
          clientId: config.clientId,
          authorizationParams: {
            audience: config.audience,
            redirect_uri: window.location.origin,
          },
          // In memory, not localStorage: an access token in localStorage is
          // readable by any script that gets onto the page.
          cacheLocation: "memory",
          useRefreshTokens: true,
        });

        // Returning from Auth0 with ?code= -- exchange it, then hand the token
        // to the server so the ordinary cookie carries the session from here.
        if (location.search.includes("code=") && location.search.includes("state=")) {
          await auth0.handleRedirectCallback();
          const token = await auth0.getTokenSilently();
          const response = await fetch("/api/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token }),
          });
          if (response.ok) {
            window.location.replace("/");
          } else {
            const body = await response.json().catch(() => ({}));
            show(body.error || "The server would not accept that sign-in.");
          }
        }

        document.getElementById("login").onclick = () =>
          auth0.loginWithRedirect();
        document.getElementById("signup").onclick = () =>
          auth0.loginWithRedirect({
            authorizationParams: { screen_hint: "signup" },
          });
      } catch (error) {
        show(
          "Could not reach Auth0 (" + error.message + "). If this machine has " +
          "no internet access, use a personal token instead."
        );
      }
    }
  </script>
"""

_PAGE = """<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Sign in · Concordance</title>
<link rel="icon" href="data:," />
<style>
  :root {
    --ground: #fcfcfd; --surface: #f3f5f7; --ink: #14171f; --muted: #59616f;
    --faint: #666c76; --hairline: #dde1e7; --accent: #0f6e72;
    --accent-soft: #e0f0f0; --bad: #a6392f; --bad-soft: #f6e3e0;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --ground: #0d0f14; --surface: #14181f; --ink: #e5e8ed; --muted: #9ba4b1;
      --faint: #7d8797; --hairline: #232932; --accent: #55c6cb;
      --accent-soft: #10312f; --bad: #e58175; --bad-soft: #2e1714;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: var(--surface); color: var(--ink); padding: 1.5rem;
    font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  main {
    width: 100%; max-width: 26rem; background: var(--ground);
    border: 1px solid var(--hairline); border-radius: 8px; padding: 1.75rem;
  }
  h1 {
    margin: 0; font-size: 1.35rem; letter-spacing: -0.01em;
    font-family: "Iowan Old Style", Charter, Georgia, serif;
  }
  .model {
    margin: 0.35rem 0 1.25rem; color: var(--faint);
    font: 12px ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  label {
    display: block; margin-bottom: 0.35rem; font-size: 0.8rem; color: var(--muted);
  }
  input {
    width: 100%; padding: 0.6rem 0.7rem; margin-bottom: 0.75rem;
    border: 1px solid var(--hairline); border-radius: 5px;
    background: var(--surface); color: var(--ink); font-size: 0.95rem;
  }
  input:focus-visible, button:focus-visible, summary:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px;
  }
  button {
    width: 100%; padding: 0.6rem 0.75rem; margin-bottom: 0.5rem; cursor: pointer;
    border-radius: 5px; font-size: 0.9rem; font-weight: 500;
    border: 1px solid var(--hairline); background: var(--ground); color: var(--ink);
    transition: background-color 150ms, border-color 150ms, filter 150ms;
    min-height: 44px;
  }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--ground); }
  button.primary:hover { filter: brightness(1.1); }
  button.secondary:hover { background: var(--accent-soft); border-color: var(--accent); }
  .note { margin: 0.5rem 0 0; font-size: 0.78rem; color: var(--faint); }
  .problem {
    margin-top: 0.85rem; padding: 0.6rem 0.7rem; font-size: 0.82rem;
    color: var(--bad); background: var(--bad-soft);
    border: 1px solid var(--bad); border-radius: 5px; overflow-wrap: anywhere;
  }
  details { margin-top: 1.25rem; border-top: 1px solid var(--hairline); padding-top: 1rem; }
  summary { cursor: pointer; font-size: 0.85rem; color: var(--muted); }
  details[open] summary { margin-bottom: 0.75rem; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
</head>
<body>
  <main>
    <h1>Concordance</h1>
    <p class="model">__MODEL__</p>
__METHODS__
  </main>
__SCRIPT__
</body>
</html>
"""

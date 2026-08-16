# Signing in to Concordance

Concordance runs unauthenticated by default. That is deliberate: the common
case is one person running `concordance serve` against a model on their own
laptop, where a login is friction protecting nothing.

Three ways in, and they compose. Pick by what you actually need.

| You need | Use | Recorded against a decision |
|---|---|---|
| Nothing — local, single user | *(default)* | `unattributed`, unverified |
| To stop anyone on the network reading the model | `--token` | *(nothing — grants access, names nobody)* |
| To know which reviewer signed something off | `--users` | Their name, **verified** |
| Real accounts, offboarding, signup, Google | `--auth0-*` | Their Auth0 identity, **verified** |

---

## Why the decision log cares

The Review queue records who accepted or corrected each low-confidence
statement. That record is worth something only if the name in it is a fact
rather than a claim.

Without identity, `author` is whatever the request body said — so a reviewer
could sign off under a colleague's name. With `--users` or Auth0, the server
resolves the name from the credential presented and **ignores the body
entirely**. Every entry also stores `author_verified`, so a log containing both
kinds can still be read honestly.

---

## Option 1 — personal tokens (`--users`)

The smallest thing that makes the audit trail trustworthy. No accounts, no
passwords, no internet.

Create a file, one entry per reviewer:

```json
{
  "Anna Poulsen": "generate-a-long-random-token-here",
  "Bo Jensen":    "and-a-different-one-for-each-person"
}
```

Generate tokens properly — they are the whole credential:

```bash
python -c "import secrets; print(secrets.token_urlsafe(24))"
chmod 600 users.json          # refused to load if others can read it
```

```bash
concordance serve model.pbix --users users.json --decisions decisions.jsonl
```

Each person opens the server and pastes their token into the sign-in page.
Refused at load: a world-readable file, a token under 16 characters, and two
people sharing one.

**What this is not.** A bearer token is not an account. There is no password to
reset, no session that expires, and no way to revoke one person without editing
the file. Whoever holds the token is treated as that person. The sign-in page
says so rather than imitating a login it is not.

---

## Option 2 — Auth0 (`--auth0-*`)

Use this when you need what a JSON file cannot give you: offboarding, password
policy, MFA, self-service signup, and social login.

**Signup and Google are not features of Concordance.** They are Auth0 Universal
Login features that appear once enabled on the tenant. There is no separate
screen here to build or configure.

### Set up the tenant

1. **Create an application.** Auth0 Dashboard → Applications → Create
   Application → **Single Page Web Application**.

2. **Register the URLs.** In that application's settings, add the address you
   serve on to all three lists. `concordance serve` defaults to port 8000; add
   every port you actually use, because Auth0 matches these exactly.

   | Field | Value |
   |---|---|
   | Allowed Callback URLs | `http://localhost:8000` |
   | Allowed Logout URLs | `http://localhost:8000/signed-out` |
   | Allowed Web Origins | `http://localhost:8000` |

3. **Create an API.** Dashboard → APIs → Create API. The **Identifier** you
   choose is the audience — for example `https://concordance/api`. It is a
   name, not a URL that has to resolve.

   This step is not optional. Without an audience Auth0 issues an *opaque*
   token rather than a JWT, and nothing can verify it. Concordance refuses to
   start rather than accept a domain with no audience.

4. **Enable Google.** Dashboard → Authentication → Social → Google. The button
   then appears on Universal Login by itself.

5. **Allow signup.** Dashboard → Authentication → Database → your connection →
   check *Disable Sign Ups* is **off**. The page's "Create an account" button
   opens Universal Login with `screen_hint=signup`.

### Run it

```bash
pip install "concordance[auth0]"      # PyJWT + cryptography, for RS256

concordance serve model.pbix \
  --auth0-domain      your-tenant.eu.auth0.com \
  --auth0-audience    https://concordance/api \
  --auth0-client-id   YOUR_SPA_CLIENT_ID \
  --decisions         decisions.jsonl
```

**Never pass a client secret.** A single-page application cannot hold one — the
browser can read anything it is given. Concordance has no flag for it.

### What the server checks

Every request's token is verified before it is trusted:

- **RS256 only.** The algorithm is fixed, never read from the token. A verifier
  that honours the token's own `alg` can be handed `alg: none`, or tricked into
  verifying an HS256 signature using the public key as the shared secret. Both
  are refused here, and both have tests.
- **Signature** against the tenant's published JWKS, selected by `kid`. An
  unknown `kid` refetches once (key rotation) and then refuses.
- **Issuer, audience and expiry** are all required. Without the audience check,
  a token minted for any other API on the same tenant would open this one.

Every failure is a refusal. There is no path that returns an identity it could
not prove.

---

## Both together

Auth0 and `--users` can be configured at the same time, and should be if any
install runs somewhere Auth0 cannot be reached:

```bash
concordance serve model.pbix \
  --auth0-domain your-tenant.eu.auth0.com \
  --auth0-audience https://concordance/api \
  --auth0-client-id YOUR_SPA_CLIENT_ID \
  --users users.json \
  --decisions decisions.jsonl
```

The sign-in page leads with Auth0 and offers the token underneath. This is not
a convenience — Concordance's normal deployment is a laptop inside a regulated
network, and making an internet round-trip mandatory to read a *local* model
would lock that person out of their own tool. If the Auth0 SDK cannot load, the
page says so plainly and the token path is still there.

---

## Known limits, stated plainly

- **The Auth0 path has not been exercised against a live tenant.** Its logic is
  tested against a locally generated RSA keypair — which is the same code path
  a real tenant takes, since Auth0's half is "publish a JWKS and sign RS256
  tokens" — but no end-to-end sign-in has been performed. The environment this
  was built in blocks `*.auth0.com` at the network layer. Treat the first real
  sign-in as the acceptance test.
- **The Auth0 SDK loads from `cdn.auth0.com`.** It is the only external request
  the interface makes, and only when a tenant is configured. An offline install
  never reaches for it.
- **No roles or permissions.** Anyone who can sign in can read the model and
  answer the review queue. Auth0 RBAC is not consulted.
- **Sessions do not expire on the Concordance side.** The credential cookie
  lasts until the browser is closed or the token is rejected; Auth0's own token
  expiry is enforced on every request, so an expired one stops working.
- **`--token` alone identifies nobody** — it is a lock on the door, not a name
  badge, and the log correctly records decisions made with it as unverified.

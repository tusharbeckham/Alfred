---
name: api-integration
description: Integrating third-party APIs/SDKs, webhooks, and OAuth flows with resilience (timeouts, retries with backoff, idempotency, rate limits) and contract testing. Use when connecting to an external service or hardening an existing integration.
---

# API Integration

## Golden rules
- Treat every external service as unreliable and hostile. It will be slow, return errors,
  change its schema, and rate-limit you — design for all of it.
- Never hardcode API keys, tokens, or secrets. Use env vars or a secret manager; never log them.
- Verify against the vendor's sandbox/staging before touching production, especially for
  mutating, billable, or irreversible calls.
- Isolate the integration behind an adapter/client so the rest of the app never speaks the
  vendor's dialect directly.

## Resilience (non-negotiable defaults)
- **Timeouts**: set connect AND read timeouts on every call. A call with no timeout is an
  outage waiting to happen.
- **Retries**: bounded (typically 3-5), only on transient failures (429, 5xx, network).
  Exponential backoff + jitter. Never retry non-idempotent calls blindly.
- **Idempotency**: send an idempotency key on create/charge/mutate calls so a retry cannot
  double-execute.
- **Circuit breaker**: stop hammering a failing dependency; fail fast and recover gracefully.
- **Rate limits**: respect `Retry-After` and rate-limit headers; throttle proactively.
- **Bulkhead / fallback**: contain failures; degrade gracefully rather than cascading.

## Authentication
- **OAuth 2.0 / OIDC**: pick the right grant — authorization code (+ PKCE) for user-facing,
  client credentials for service-to-service. Request least-privilege scopes.
- Store tokens securely; refresh before expiry; handle refresh failure and revocation.
- Validate webhook signatures (HMAC) — never trust an inbound webhook payload unverified.

## Contract testing
- Pin to an API version. Assert the response schema you depend on (consumer-driven contract).
- A contract test should fail loudly when the provider changes shape — before production does.
- Snapshot/replay real responses for deterministic tests; do not hit the live API in unit tests.

## Webhooks (inbound)
- Verify signature, then check timestamp/nonce to reject replays.
- Respond fast (2xx) and process async; the sender will retry on timeout — so be idempotent.

## Definition of done
- Timeouts, bounded retries with backoff, and idempotency are in place and tested.
- Secrets come from config/env, never code. Failure paths (down, 429, 5xx, malformed) are handled.
- Contract test guards the response shape. Sandbox-verified before prod.

## Anti-patterns
- No timeout. Unbounded/aggressive retries (a self-inflicted DDoS). Retrying non-idempotent writes.
- Hardcoded keys; secrets in logs. Trusting webhook payloads without signature verification.
- Parsing the whole vendor response into your domain with no anti-corruption layer.

# The Alfred Harness

> One policy-gated entrypoint for automating this machine from `C:\Alfred`.
> **Deny by default.** Nothing runs unless the *signed* policy explicitly allows it for
> that caller. Built so that an untrusted local model cannot use it to do harm.

```powershell
harness verify                                   # is the policy intact?
harness list --caller owner                      # what can I run?
harness run status                               # run a capability
harness run git-status --param path=C:\Alfred
harness run backup --approve                     # gated: needs explicit approval
```

`harness.cmd` wraps `python scripts/harness.py`. Nothing else in Alfred changed — the
harness is purely additive.

## Why it is safe

Five independent controls. An attacker (or a badly-behaved local model) has to beat all of them.

**1. Signed policy — the anti-tampering control.**
`policy/harness-policy.json` is HMAC-SHA256 signed into `policy/harness-policy.sig`. The key
lives in `secrets/harness.key`, which every agent is denied read and write access to.
A local model *can* edit the policy file — and the moment it does, the signature stops
matching and the harness refuses to run **anything at all**:

```
POLICY ERROR: POLICY INTEGRITY FAILURE — harness-policy.json does not match its signature.
```

So a model cannot grant itself new powers. Fail closed, not open.

**2. Deny by default + per-caller allowlists.**
Callers are named roles, each with an explicit capability list:

| Caller | Trust | Auth required | Gets |
|---|---|---|---|
| `owner` | high | no | everything, gated items still need `--approve` |
| `kiro-agent` | medium | no | reads, memory, index, tests, lint, CI, workflow *plans* |
| `local-model` | **untrusted** | **yes** | 5 read-only diagnostics. No writes, no network, no agents. |
| `scheduled` | low | yes | project work only; every gated capability refused |

A capability not in the policy cannot run, whoever asks.

**3. The untrusted role needs a token it has no way to get.**
`local-model` is `authRequired`, so it needs a bearer token whose SHA-256 matches an entry in
`secrets/harness-callers.json` — a file it cannot read. With no token provisioned it gets
nothing. Even *with* a valid token, its allowlist still binds: all five permitted capabilities
are `risk: read`, and there is a test asserting that stays true.

**4. No shell string is ever built.**
Commands are argv arrays run with `shell=False`. Parameters are validated against declared
types before substitution — enums are exact-match, strings are length-capped and reject
control characters, paths are resolved and confined to `C:/Alfred` and `C:/projects` and
refused if they touch `C:/Windows`, Program Files, `secrets/`, `policy/`, `.env`, or key
material. A payload like `x & echo pwned > file` is inert data, not a command. Then a
forbidden-pattern scan runs over the final argv as defence in depth.

**5. Everything is audited.**
Every attempt — executed, denied, or dry-run — appends a JSON line to
`memory/harness-audit.jsonl` with caller, trust, capability, risk, argv, and outcome.

## Capabilities

Read-only (safe for automation): `status`, `doctor`, `recall`, `disk-report`,
`process-report`, `git-status`, `lint`, `index`, `workflow-plan`.
Write/execute: `remember` (memory only), `test`, `ci`.
**Gated** (high-trust caller + `--approve`): `workflow-run` (spawns agents, spends credits),
`backup`, `git-commit` (commits only — never pushes, refuses `main`/`master`, refuses
staged files that look like secrets).

## Adding a capability

1. Add an entry under `capabilities` in `policy/harness-policy.json`: `command` plus an
   `args` **array** with `{placeholder}` params, a `risk`, and `gated: true` if it mutates
   anything outside `memory/`.
2. Declare every parameter under `params` with a type (`enum` / `path` / `string`). Never
   accept a free-form string that lands in a command position.
3. Grant it to the narrowest caller that needs it. Do **not** give anything to `local-model`
   that is not `risk: read`.
4. Re-sign: `python scripts/harness.py sign` (Owner only — needs the key).
5. `python scripts/test_harness.py` must stay green.

## Verification status

`python scripts/test_harness.py` → **25 tests, OK**. Covers policy-tamper detection,
untrusted-caller containment (including with a valid token), deny-by-default, gating,
parameter/path validation, shell-injection inertness, and audit logging.

## Threat model — what this does NOT protect against

Stated plainly:

- A model that can run **arbitrary shell** does not need the harness; the harness is a safe
  road, not a fence around the machine. Keeping shell away from untrusted models is done in
  the agent configs (`toolsSettings.shell`), not here.
- Anyone who can read `secrets/harness.key` can re-sign a hostile policy. Protect that file;
  it is git-ignored and agent-denied, and it should stay that way.
- The Owner's own `--approve` is trusted by design. The harness makes gated actions explicit
  and logged; it cannot second-guess a deliberate human decision.

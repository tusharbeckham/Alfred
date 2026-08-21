# Security

Alfred runs commands on the machine it is installed on. That is its purpose, and it
is also the reason this file exists. Read this before running it anywhere you care
about.

## What this software can do

The harness (`harness.cmd`) executes real capabilities against the local system:
git operations, file inspection, backups, scheduled tasks. Nothing runs unless a
**signed policy** permits it for the calling identity, but the ceiling is still
"whatever the policy allows". Treat the policy as the security boundary and read
`policy/harness-policy.json` before trusting a clone of this repository.

The full threat model, including why each control exists and what it does not
protect against, is in [`docs/harness.md`](docs/harness.md).

## What is deliberately not in this repository

None of the following is committed, and none of it should ever be:

- `secrets/` — the HMAC key that signs the capability policy, and any API keys.
- `memory/` — the memory graph, episodic memory, and the append-only audit trail.
- `.kiro/brains/*/memory/` — per-agent episodic notes.
- Fine-tune datasets, eval outputs, and model weights.

If you clone this, you generate your own signing key. There is no shared secret to
leak, and a policy edited without the key fails closed: the harness refuses to run
anything at all.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem.

1. Preferred: open a private advisory through GitHub's *Security* tab
   (Report a vulnerability).
2. Otherwise: email `tusharentheoria@gmail.com` with `SECURITY` in the subject.

Include what you did, what happened, and what you expected. A proof of concept is
welcome but not required.

I am one person maintaining this alongside a degree, so I will not promise a
response time I cannot keep. I will acknowledge a genuine report as soon as I read
it, and I would rather hear about a problem late than not at all.

## Scope

In scope: the harness policy and its enforcement, the dashboard's authentication
and read-only guarantees, the signing and verification path, anything that lets an
untrusted local model escalate its own capabilities.

Out of scope: vulnerabilities that require an attacker to already have the signing
key or administrator rights on the machine, and the security of third-party model
providers.

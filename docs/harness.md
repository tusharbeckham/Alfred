# The Alfred Harness

> One policy-gated entrypoint for automating this machine from `C:\Alfred`.
> **Deny by default.** Nothing runs unless the *signed* policy explicitly allows it for
> that caller. Built so that an untrusted local model cannot use it to do harm.

```powershell
harness verify                                   # is the policy intact?
harness lint                                     # does the policy MEAN what it says?
harness diff                                     # what changed since the last signature?
harness sign --review                            # refuse to bless a privilege increase
harness review-log                               # was the last signature ever reviewed?
harness list --caller owner                      # what can I run?
harness run status                               # run a capability
harness run git-status --param path=C:\Alfred
harness run backup --approve                     # gated: needs explicit approval
harness audit-verify                             # has the audit trail been rewritten?
harness seal-legacy                              # seal the pre-chain records
harness checkpoint                               # witness the trail's current head
harness mint-token local-model --scope status --ttl 900
harness revoke --caller local-model              # break glass: kill every issued token
harness quota                                    # how much budget has each caller left?
```

`harness.cmd` wraps `python scripts/harness.py`. Nothing else in Alfred changed — the
harness is purely additive.

## Why it is safe

Sixteen independent controls. An attacker (or a badly-behaved local model) has to beat all of them.

The first five are the original design. Controls 6-16 were added after reviewing how this
class of system actually gets broken; each one names the specific attack it answers, because
a control whose threat you cannot state is decoration.

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
| `local-model` | **untrusted** | **yes** (scoped) | 8 read-only diagnostics. No writes, no network, no agents. Rate-limited 20/min. |
| `scheduled` | low | yes | project work only; every gated capability refused |

A capability not in the policy cannot run, whoever asks.

**3. The untrusted role needs a token it has no way to get — and that token is narrow.**
`local-model` is `authRequired`, and since v1.1 it is also `requireScopedToken`: a flat shared
secret is refused for that caller. Tokens are minted by the Owner and carry the caller, an
expiry and the exact capability list **inside the HMAC**:

```powershell
harness mint-token local-model --scope graph-recall --ttl 900
```

The threat this answers is the **confused deputy**. The old flat token never expired and was
bound to nothing, so a token issued so the model could read the memory graph authenticated a
call to `graph-assert` equally well — a prompt injection only had to talk the model into
spending its legitimate credential on a capability the *attacker* chose. Now the capability
name is signed, so widening the scope or pushing the expiry out is forgery, not escalation.
A token also cannot grant more than the policy already does; `mint-token` refuses to issue
one for a capability the caller cannot run.

Even with a perfectly valid token, the allowlist still binds. Every capability
`local-model` holds is `risk: read`, and there is a test asserting that stays true.

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

**6. Paths are resolved before they are judged (`scripts/harness_guards.py`).**
On Windows a path *string* and the file it opens are not the same thing, and every trick
below exists to make them differ. Pattern-matching an unnormalized path is matching the
attacker's spelling rather than the target, so `safe_resolve` runs **first** and refuses:

| Trick | Why it works | Answer |
|---|---|---|
| `\\?\C:\...`, `\\localhost\C$\...` | `\\?\` disables the very normalization confinement depends on | UNC and extended-length paths refused |
| `notes.txt:payload` | NTFS alternate data stream — the visible path passes, the bytes land somewhere nobody inspects (the CVE-2025-8088 shape) | a `:` outside the drive separator is refused |
| `C:\Alfred\CON`, `nul.txt` | DOS device names are magic in *every* directory and never touch the filesystem; a read can block forever | every component checked against the device list |
| `C:\Alfred\secrets.\key` | Windows strips trailing dots and spaces, so the path checked is not the path used | refused (`.` and `..` exempted — they are legitimate) |
| `C:\PROGRA~1\...` | 8.3 short names resolve elsewhere than the prefix check expects | expanded via `GetLongPathNameW`, refused if unresolvable |
| a junction pointing out of the workspace | any user can create one without admin; `Path.resolve()` historically did not follow them | `os.path.realpath`, so confinement judges the real target |
| `café` written two ways | two byte sequences that display identically gave two answers | NFC normalization first |

**7. Child output is capped.**
`subprocess.run(capture_output=True)` buffers a child's *entire* output in the parent's
memory, so "write a lot to stdout" was a denial of service against the harness — available
to any caller allowed to run any capability at all, including the untrusted one, whose
diagnostics produce output sized by the state of the machine rather than by the policy.
Output is now bounded (`settings.maxOutputBytes`, 4 MiB) and truncation is **reported**
rather than passed off as a complete result.

**8. The audit trail is hash-chained, and its limit is stated.**
Each record carries the hash of the one before it. Edit or delete anything inside the file
and `harness audit-verify` names the exact line where history was rewritten.

What this does **not** stop: whoever can append to the log can also cut off its tail and
continue a chain that verifies perfectly. That requires a witness the attacker cannot
reach, which is what `harness checkpoint` writes to a separate file. The gap is documented
here rather than left for someone to discover.

Records written before chaining existed are reported as `legacy` — counted, and explicitly
not claimed as verified.

Parameter *values* are also filtered through an allowlist (`settings.auditLoggableParams`)
before being written. The trail is append-only, so a secret logged once is logged forever;
a denylist of secret-shaped regexes would have to be right about every parameter added in
future, whereas an allowlist withholds tomorrow's parameter by default. Names are kept —
knowing a `token` parameter was supplied is useful and reveals nothing.

**9. Automated callers have a budget.**
Per-caller token buckets (`rateLimit: {perMinute, burst}`) bound a runaway loop: 20/min for
`local-model`, 60/min for `scheduled`, 120/min for `kiro-agent`, unlimited for the Owner at
a keyboard. The check sits **after** the allowlist, so being refused does not cost a caller
its budget, and **before** the gate, so an approval prompt cannot be hammered for free.
`harness quota` shows what is left.

**10. The child process is confined (`scripts/harness_confine.py`).**
Time and output were bounded; **memory and CPU were not**, and nothing bounded a child's
*descendants* at all — killing a child on timeout left its grandchildren running,
unparented and outside the audit trail. `subprocess` cannot express any of that, so the
child now runs inside a **Windows Job Object** carrying a memory ceiling (per-process *and*
per-job, so ten copies of a child cannot evade a per-process limit by division), an
active-process cap, and a CPU-time ceiling. Limits scale with trust:

| Trust | Process memory | Job memory | Processes | CPU |
|---|---|---|---|---|
| `untrusted` | 256 MiB | 384 MiB | 8 | 60 s |
| `low` | 1 GiB | 1.5 GiB | 32 | 900 s |
| `medium` | 2 GiB | 3 GiB | 64 | 1800 s |
| `high` | 6 GiB | 8 GiB | 128 | 3600 s |

Two design points worth stating because they are where this normally goes wrong:

- **There is no assign-after-spawn race.** The obvious implementation — spawn, then attach
  to the job — leaves a window in which the child is already executing and can fork a
  grandchild that is never confined. The window is small, which is not the same as closed.
  So the child is created **suspended**, attached to the job before it executes a single
  instruction, and only then resumed. `subprocess` will not hand back the thread handle, so
  the thread is located by snapshotting system threads and matching the owner PID.
- **A confinement failure means nothing ran.** If attaching or resuming fails, the child is
  terminated while still suspended and the harness returns exit code **125**. Quietly
  running unconfined would turn a configured control into a suggestion.

`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` is always set, so the child tree cannot outlive the
harness even if the harness is killed rather than exiting cleanly. Peak memory and process
counts are read back into the audit record, because a limit you cannot observe is a limit
you cannot audit.

**11. Tokens can be revoked, not merely expired.**
Expiry is not revocation: a token that leaks in minute one of a one-hour TTL is usable for
fifty-nine minutes. Two granularities, because the useful one depends on what you know:

```powershell
harness revoke --token <token>          # this exact credential
harness revoke --caller local-model     # EVERY token already issued for that caller
harness revocations                     # what is currently revoked
```

The second is the break-glass case, and it is why tokens carry an issued-at claim: when a
credential leaks you usually know *which caller* is compromised, not which token. One
timestamp kills the whole generation while freshly minted tokens keep working, so the
response to a leak is not an outage.

Two details that were bugs first:

- The epoch is recorded as `now + 1`, not `now`. Timestamps have one-second resolution, so
  a token minted in the same second as the revocation had `iat == epoch` and survived —
  and that is exactly the token most likely to be the leaked one, since a leak and its
  response happen close together. Rounding up revokes marginally too much rather than too
  little.
- `harness revoke --token` **verifies the token's signature before revoking it**. Accepting
  an unverified token would let anyone stuff invented nonces into a file that is read on
  every authenticated call.

Revoked nonces are pruned once the token would have expired anyway, so the store cannot
grow without bound. It lives in `secrets/` because it is integrity-sensitive rather than
confidential: a caller able to edit it could un-revoke a credential it had just lost.

**12. Confinement works on Linux too, with different guarantees stated plainly.**
On POSIX the mechanism is `setrlimit` applied between fork and exec, plus a new session so
the whole process group can be signalled. The differences from a Job Object are real and are
not smoothed over:

| Limit | Windows | POSIX | Note |
|---|---|---|---|
| Process memory | `ProcessMemoryLimit` | `RLIMIT_AS` | POSIX caps *virtual* address space, not resident memory |
| Whole-tree memory | `JobMemoryLimit` | — | no POSIX equivalent |
| Process count | `ActiveProcessLimit` (per job) | `RLIMIT_NPROC` (per **UID**) | same intent, coarser instrument — it counts the user's other processes too |
| CPU | `PerJobUserTimeLimit` | `RLIMIT_CPU` | equivalent |
| **File size** | — | `RLIMIT_FSIZE` | the one place **POSIX is stronger**: a genuine per-file disk bound |
| Reap the tree | kill-on-close, by the kernel | `killpg` by us | weaker: a harness that is itself SIGKILLed cannot signal anything |

Soft and hard limits are set to the same value, because a child that can call `setrlimit` to
raise its own soft limit back up is not confined.

Verified for real rather than assumed: `wsl -e python3 scripts/verify_posix_confine.py` runs
13 checks that each spend a real resource — including a **control** proving the same 400 MB
allocation succeeds when the limit is absent, and a check that a child cannot raise its own
limit. All 13 pass on a Linux kernel.

**13. Signing is a reviewed act, not a rubber stamp.**
This was the weakest human step in the whole design. `harness sign` computed an HMAC over
whatever bytes were on disk and reported nothing about what they contained. A model that edits
the policy cannot forge a signature — **but it does not need to.** It only needs the Owner to
re-sign for some unrelated reason, at which point the edit is blessed for free.

```powershell
harness diff                                  # what changed since the last signature?
harness sign --review                         # refuses if the change grants new privilege
harness sign --review --accept-escalation     # ...unless that is intended
```

To diff against what was signed you need what was signed, and a signature is a fingerprint
rather than a copy — so `sign` now also writes `policy/harness-policy.signed.json`. That
snapshot needs no protection of its own because it is **self-verifying**: a snapshot whose
HMAC matches the current `.sig` *is* the signed content, and one that does not is refused
rather than believed.

The diff is **semantic, not textual**. Reindentation, key reordering and allowlist reordering
are not changes; a caller quietly gaining a capability is. A textual diff would have reported
all four and trained the reviewer to skim. Each change is classified by direction, so removing
a grant is not reported as an escalation — a review tool that cries wolf gets ignored.

Demonstrated against the actual attack: a policy edited to give `local-model` a write
capability, drop its token requirement and promote it to `medium` produces

```
ESCALATION: caller.local-model.grants.remember - a caller gained a capability
ESCALATION: caller.local-model.authRequired   - a restriction was switched off
ESCALATION: caller.local-model.trust          - a caller was promoted
REFUSING TO SIGN: this change grants new privilege (3 finding(s) above).
```

Lint and diff are independent objections and both can refuse: lint catches a policy that
cannot mean what it says, the diff catches one that means something new.

**14. The pre-chain audit records are sealed.**
`audit-verify` honestly reported 1237 records the chain did not cover — which meant they could
be edited freely while the command still said "ok". They cannot be retroactively chained,
because chaining means each record embeds the previous hash and adding that would mean
rewriting them; rewriting an audit trail to make it verifiable is self-defeating.

```powershell
harness seal-legacy      # one HMAC over the whole pre-chain region
harness audit-verify     # now reports the seal as well as the chain
```

The seal is an **HMAC with the signing key**, not a bare hash, and that is the entire point: a
plain hash stored next to the log is no defence, because whoever edits a record can recompute
it and update the anchor. The claim it supports is narrow and stated as such — it proves the
region has not been altered *since the seal*. It does not prove those records were true when
written, and it does not turn them into chained records.

`audit-verify` now fails on either a broken chain **or** an altered pre-chain region;
previously only the chain could fail it, which left the older half of the trail outside the
verdict entirely.

**15. Egress is declared per capability, and enforced on Linux.**
Every capability states whether it needs the network. On Linux a `network: false` capability
runs inside a fresh network namespace and cannot reach anything — including loopback, because a
new namespace's `lo` exists but is DOWN:

```
unshare --user --map-current-user --net -- <capability argv>
```

Three parts, each for a reason. `--user` creates a user namespace first, which is what makes
the rest work **without root** — a bare `unshare --net` returns "Operation not permitted".
`--map-current-user` keeps the child's uid; the more common `--map-root-user` would make it
believe it is uid 0, which is harmless on the host but would send a script that branches on
`geteuid() == 0` down a privileged path. `--net` is the isolation. It is still an argv array
with the real command after `--`, so the no-shell guarantee is unchanged.

The classification is **evidence-based, not guessed**: every capability's script was grepped
for network markers, and where the grep and the behaviour disagreed the behaviour won. That
correction mattered — `graph-current`, `graph-history`, `graph-assert` and `graph-doctor` were
initially marked network-needing because `memgraph.py` contains `urllib` and `localhost` at
module level, but `embed()` is only reached from the `recall` branch. Checking the call sites
instead of the file moved four capabilities into isolation and cut the untrusted caller's
network reach from three capabilities to one.

15 of 25 capabilities are egress-isolated. The one remaining exposure for the untrusted
caller is `graph-recall`, which embeds its query through LM Studio on loopback; `harness lint`
reports it as a warning every run rather than letting it become invisible, and a test asserts
the list stays exactly `["graph-recall"]`.

On **Windows this is declaration and audit only.** There is no equivalent short of
AppContainer, so the `egress` stage in the control chain says which of the two happened rather
than implying the stronger one everywhere.

**16. The signing history is inspectable.**
Control 13 answers "what changed since the last signature?". It cannot answer "was that
signature itself ever reviewed?" — and the first signature on any clone establishes a baseline
nobody vetted. That gap cannot be closed, but it does not have to be invisible:

```powershell
harness review-log
```

Every signing appends to a hash-chained ledger in `secrets/`: when, whether `--review` was
used, which escalations were accepted by name, and how many claims changed. So the provenance
of the current policy is something you can look up instead of remember, and an unreviewed
baseline is a reported fact rather than an unexamined assumption.

Recorded as unreviewed is not the same as wrong — signing without `--review` is perfectly
reasonable for a change you just made deliberately. It means no diff was examined at that
point, which is worth being able to establish afterwards.

## Red-teaming the claims

Sixteen controls, each with a stated threat. The unit suites test the *mechanisms*; this tests
the *claims*, by attempting each attack and expecting refusal:

```powershell
python scripts/redteam_harness.py            # exit 0 means every attack was refused
python scripts/redteam_harness.py --verbose  # also print each refusal reason
```

**66 probes across 19 control families, all holding.** It tampers with the policy and confirms
the harness fails closed, invents capabilities and callers, aims a scoped token at the wrong
capability, replays it as another caller, flips one character of its MAC, reuses a revoked one,
smuggles six shell payloads through parameters, points a path capability at UNC paths, alternate
data streams, device names, 8.3 names, the signing key and `C:\Windows`, drains a quota bucket,
edits and deletes and truncates audit records, launders a pre-chain edit by resealing with a
foreign key, and tries to get a hostile policy blessed by a legitimate re-sign.

Why this is worth having on top of the unit tests: a mechanism can keep working while the
property it was supposed to give you quietly stops holding. A path check still rejects bad paths
after the capability stopped taking paths at all; a quota still counts after the caller lost the
capability it was bounding. Asking "was this attack refused?" notices that. Asking "does this
function still return False?" does not.

Three design points, each of which it got wrong first:

- **A refusal for the wrong reason is not a pass.** Each probe matches the *reason*, not just a
  non-zero exit. That caught a stale probe of mine that aimed `ultron-pipeline` at `kiro-agent`:
  the refusal came from the allowlist rather than the gate, because the dead grant had been
  removed earlier. The gate's trust half is now asserted structurally instead — no caller below
  high trust holds a gated capability, which is exactly what `harness lint` enforces.
- **Every negative result is paired with a control.** "The confined child failed to allocate"
  means nothing without "the unconfined one succeeded".
- **Tamper-detection is tested on a copy.** Corrupting the real audit trail to prove corruption
  is detectable is not a trade worth making. Everything that mutates state backs it up and
  restores it in a `finally`.

The first run also found a real calibration fact rather than a bug: a naive loop of 40 real CLI
calls does **not** exhaust a 20/min bucket, because each subprocess takes over a second and the
refill claws back most of what the loop spends. At ~1.5 s per call it takes roughly 60 calls and
a full minute. The probe now drains the bucket deterministically and then checks the CLI honours
it — and also checks the bucket refills, because throttling that never lifts is a lockout.

`scripts/test_harness_guards.py` asserts this suite still exists and still probes every control
family, so a red team nobody runs cannot silently become a red team that does not exist.

## Does the policy mean what it says?

A signature proves nobody tampered with the policy. It does not prove the policy says what
its author intended. `harness lint` checks the meaning:

```powershell
harness lint            # errors fail the command; --strict fails on warnings too
```

It catches grants of capabilities that do not exist, gated capabilities granted to callers
whose trust level guarantees the gate will refuse them, write-risk capabilities granted to
untrusted callers, wildcards outside high trust, argv placeholders that are not declared as
params (so they can never resolve), params declared but never used in argv (so they are
never validated and only *look* like a control), and capabilities no caller can reach.

This found a real bug on its first run: `kiro-agent` (trust=medium) was granted
`ultron-pipeline`, which is gated and therefore high-trust-only. Confirmed empirically —

```
DENIED: 'ultron-pipeline' is a gated capability and requires a high-trust caller;
        'kiro-agent' is trust=medium.
```

The grant had never been usable, and `harness list --caller kiro-agent` had been
advertising a power that agent did not have. It was removed and the policy re-signed.
`test_harness_guards.py` now asserts the live policy lints clean, so it cannot drift back.


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
5. `python scripts/harness.py lint` must report no errors. This is the step that catches a
   grant the gate would always refuse, or a placeholder you forgot to declare.
6. `python scripts/test_harness.py` and `python scripts/test_harness_guards.py` must stay green.

## Verification status

- `python scripts/test_harness.py` → **41 tests, OK**. Policy-tamper detection,
  untrusted-caller containment (including with a valid token), scoped-token scope/expiry/
  replay refusal, reviewed signing (the full escalation scenario, plus a check that a
  *tightening* change is not flagged and that reformatting is not a change),
  deny-by-default, gating, parameter and path validation,
  shell-injection inertness, and audit logging.
- `python scripts/test_harness_guards.py` → **107 tests, OK**. Every Windows path trick in
  control 6, output caps, the audit chain (including an explicit test that tail truncation
  is *not* caught by the chain alone), legacy sealing, redaction, quotas, token forgery,
  revocation, the policy linter, and a regression guard asserting the live policy lints clean.
  The confinement tests **spend real memory**: a child asking for 400 MB under a 64 MB job cap
  gets `MemoryError`, and a control test proves the same allocation succeeds without a
  limit — so the ceiling is demonstrably enforced by the kernel rather than by a config file.
- `wsl -e python3 scripts/verify_posix_confine.py` → **18 checks, all pass** on a real Linux
  kernel. The POSIX branch cannot be exercised from Windows, so it is verified where it runs
  instead of shipped hopeful. A test in the guards suite asserts this verifier still exists and
  still checks the things that matter, so the branch cannot end up unverified while the suite
  looks green.
- `python scripts/test_ultron_parity.py` → **30 tests, OK**. Includes 17 cross-engine guard
  parity tests: Alfred verifies an audit chain Ultron wrote and vice versa, both engines
  name the same line as a break, scoped tokens are interchangeable in both directions, both
  honour the same revocation state and agree on the caller-epoch boundary, and both refuse
  the same Windows path tricks.
- `python scripts/harness_confine.py` runs a standalone Windows self-test for a machine whose
  behaviour you doubt.

## One guarantee, two engines

Ultron (`C:\projects\ultron-cli`) carries the same guards in `src/guards.mjs`, with the same
names and the same wire format. That is deliberate, and it is the same argument as the
gauntlet router parity: a bound Alfred enforces and Ultron does not is a bound you escape by
typing a different binary, and an audit trail only one engine can read is a trail the other
engine has to be *trusted* about.

Making that real required pinning canonical JSON to sorted keys with compact separators —
the one serialization Python and `JSON.stringify` produce identically without configuration.
`chain_verify` still accepts the earlier spaced form so records written before the change
stay verifiable rather than being reported as tampered.

Process confinement is deliberately **not** mirrored: Job Objects are a Windows kernel
facility and `setrlimit` a POSIX one, and Node exposes neither. Claiming parity where none
exists would be worse than the gap. Ultron bounds output and wall-clock time; it does not
bound child memory.

## Threat model — what this does NOT protect against

Stated plainly:

- A model that can run **arbitrary shell** does not need the harness; the harness is a safe
  road, not a fence around the machine. Keeping shell away from untrusted models is done in
  the agent configs (`toolsSettings.shell`), not here.
- Anyone who can read `secrets/harness.key` can re-sign a hostile policy, **mint valid scoped
  tokens, and forge the legacy-audit seal**. Protect that file; it is git-ignored and
  agent-denied, and it should stay that way. Every cryptographic control here reduces to it.
- **Tail truncation of the audit log** is not detectable from the log alone (control 8).
  `harness checkpoint` is the answer, and it is a declared capability (`audit-checkpoint`) the
  `scheduled` caller may run unattended — so it no longer depends on the Owner remembering. It
  still only works as often as it actually runs.
- **The legacy seal proves absence of change since the seal, and nothing more.** It says
  nothing about whether those 1237 pre-chain records were accurate when written. That is
  unknowable now, and is not claimed.
- **Confinement bounds different things on each platform** (control 12). On Windows there is no
  file-size limit, so a capability permitted to write can still fill a disk — disk is
  *accounted* in the audit record, not bounded. On POSIX there is no whole-tree memory limit,
  and `RLIMIT_NPROC` counts per user rather than per process tree.
- **Egress isolation is Linux-only** (control 15). On Windows the `network: false` declaration
  is recorded in the audit trail and enforced by nothing. AppContainer would enforce it and is
  not implemented, so on Windows a compromised declared binary could still reach the network.
- **The untrusted caller retains one network-capable capability**, `graph-recall`, because
  embedding a query needs LM Studio on loopback. `harness lint` reports it every run and a test
  pins the list, so it is an accepted risk rather than an oversight.
- `sign --review` compares against the last *signed* state and cannot tell you whether that
  baseline was itself reviewed. `harness review-log` (control 16) makes the history inspectable,
  which surfaces the gap rather than closing it: a clone's first signature is always unreviewed,
  and the command reports `review: unavailable` rather than implying otherwise.
- Scoped tokens can be revoked (control 11), but revocation fails **open** if the store is
  unreadable — chosen so a corrupt write cannot lock the Owner out, and named here rather than
  buried.
- The Owner's own `--approve` and `--accept-escalation` are trusted by design. The harness
  makes gated actions explicit, diffed and logged; it cannot second-guess a deliberate human
  decision.

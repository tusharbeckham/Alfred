# Contributing

This is a personal system that happens to be public. Issues and questions are
genuinely welcome — if something is unclear, that is a documentation bug and I want
to know. Pull requests are welcome too, with one honest caveat: this repository
encodes one person's working preferences, so a change that is right for you may not
be right for it, and I may decline a perfectly good patch on those grounds.

## Running it

Requirements: **Python 3.11+ and nothing else.** No `pip install`, no `npm install`,
no cloud account. If a change needs a dependency, that is the thing to discuss
first, because working offline on a fresh clone is a design constraint rather than a
happy accident.

```powershell
git clone https://github.com/tusharbeckham/Alfred.git
cd Alfred
python scripts/harness.py verify     # is the policy intact?
alfred status                        # probe every subsystem
```

The optional pieces — LM Studio for the local model, Piper for the offline voice,
Kiro CLI for the agent layer — are documented in `docs/`. Everything degrades
without them instead of crashing, and the tests do not need any of them.

## The tests

```powershell
Get-ChildItem scripts/test_*.py | ForEach-Object { python $_.FullName }
```

Every suite must exit `0`. Suites that need something absent from the machine —
Node, an Ultron checkout, LM Studio — skip cleanly rather than failing, because a
red suite should mean a real defect. CI runs exactly the loop above on Windows.

A change to behaviour comes with a test. A change to the harness policy, the
signing path, or the gate router comes with a test that proves the *refusal* still
happens, not only that the happy path works.

## House style

- **Standard library only.** See above.
- **Fail loudly.** A script that half-applies a change and exits `0` is worse than
  one that stops with a readable message and a non-zero code.
- **Comments explain why, not what.** The code already says what it does. Write down
  the reasoning a future reader would otherwise have to reconstruct — especially the
  option you rejected and the reason.
- **No fabricated output.** Never print a result that was not measured. If something
  is unknown, the honest word is `unknown`, not `0`.
- **Safety gates are not suggestions.** Destructive, system-level, production, or
  secret-touching actions stay behind an explicit approval. Do not add a code path
  that works around one.

## What never gets committed

`secrets/`, `memory/`, `.kiro/brains/*/memory/`, fine-tune datasets, eval results,
model weights. These are ignored in `.gitignore`; if you find something personal
that is tracked, that is a bug worth reporting privately (see
[SECURITY.md](SECURITY.md)).

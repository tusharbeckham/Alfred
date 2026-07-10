#!/usr/bin/env python3
"""
Alfred eval scorer + regression gate - deterministic, offline, stdlib-only.

Makes the improvement loop BITE: instead of subjective agent review, score model
responses against machine-checkable checks (must_include / must_avoid, regex,
case-insensitive), then gate prompt changes on hard numbers per evals/rubric.json.

Subcommands
-----------
score : score responses (a {case_id: text} JSON) against a suite's checks. Cases
        without checks are reported as 'manual' (left to the evaluator agent) and
        excluded from the deterministic aggregate. Writes a scored results JSON.

gate  : compare two scored results (--before/--after) using rubric.json's
        acceptance_rule - the targeted --category must improve AND no other category
        may regress below (prior - tolerance). Exit 0 = ACCEPT, 1 = REVERT.

Examples
--------
  python scripts/eval-score.py score --suite evals/coding-evals.json \
      --checks evals/coding-checks.json --responses evals/results/resp.json
  python scripts/eval-score.py gate --before before.json --after after.json --category security
"""
import argparse, json, re, sys, os, datetime


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def score_case(checks, response):
    """Return dict with raw score 0..1, or None if no checks (manual case)."""
    if not checks:
        return None
    text = response or ""
    inc = checks.get("must_include", []) or []
    avo = checks.get("must_avoid", []) or []
    found, missing = [], []
    for pat in inc:
        (found if re.search(pat, text, re.IGNORECASE) else missing).append(pat)
    avoided_hits = [p for p in avo if re.search(p, text, re.IGNORECASE)]
    inc_score = (len(found) / len(inc)) if inc else 1.0
    penalty = (len(avoided_hits) / len(avo)) if avo else 0.0
    raw = max(0.0, inc_score - penalty)
    return {"raw": round(raw, 4), "found": found, "missing": missing, "avoided_hits": avoided_hits}


def do_score(args):
    suite = load(args.suite)
    checks_map = load(args.checks) if args.checks else {}
    responses = load(args.responses) if args.responses else {}
    scored, manual = [], []
    num = den = 0.0
    cats = {}
    for c in suite.get("cases", []):
        cid = c.get("id"); w = float(c.get("weight", 1.0)); cat = c.get("category", "uncategorized")
        chk = c.get("checks") or checks_map.get(cid)
        r = score_case(chk, responses.get(cid, ""))
        if r is None:
            manual.append(cid); continue
        cs = r["raw"] * w
        num += cs; den += w
        cats.setdefault(cat, [0.0, 0.0])
        cats[cat][0] += cs; cats[cat][1] += w
        scored.append({"id": cid, "category": cat, "weight": w, "raw": r["raw"],
                       "weighted": round(cs, 4), "passed": r["raw"] >= args.pass_threshold,
                       "missing": r["missing"], "avoided_hits": r["avoided_hits"]})
    aggregate = round(num / den, 4) if den else 0.0
    by_cat = {k: round(v[0] / v[1], 4) for k, v in cats.items() if v[1]}
    result = {"suite": suite.get("suite"), "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "pass_threshold": args.pass_threshold, "aggregate": aggregate, "by_category": by_cat,
              "scored_cases": len(scored), "manual_cases": manual, "cases": scored}
    out = args.out
    if not out:
        os.makedirs("evals/results", exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = f"evals/results/auto-{suite.get('suite', 'suite')}-{stamp}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"suite={result['suite']} aggregate={aggregate} (threshold {args.pass_threshold}) "
          f"scored={len(scored)} manual={len(manual)}")
    for k, v in sorted(by_cat.items()):
        print(f"  [{'PASS' if v >= args.pass_threshold else 'FAIL'}] {k}: {v}")
    fails = [c["id"] for c in scored if not c["passed"]]
    if fails:
        print("  failing: " + ", ".join(fails))
    if manual:
        print("  manual (needs agent review): " + ", ".join(manual))
    print(f"  wrote {out}")
    return 0


def do_gate(args):
    before, after = load(args.before), load(args.after)
    b, a = before.get("by_category", {}), after.get("by_category", {})
    tol, cat = args.tolerance, args.category
    print(f"gate: targeted category='{cat}', tolerance={tol}")
    print(f"{'category':22}{'before':>9}{'after':>9}{'delta':>9}")
    improved_target = False
    regressions = []
    for k in sorted(set(list(b) + list(a))):
        bv, av = b.get(k), a.get(k)
        dv = (av - bv) if (bv is not None and av is not None) else None
        bs = f"{bv:.4f}" if bv is not None else "-"
        as_ = f"{av:.4f}" if av is not None else "-"
        ds = f"{dv:+.4f}" if dv is not None else "-"
        print(f"{k:22}{bs:>9}{as_:>9}{ds:>9}")
        if k == cat and dv is not None and dv > 0:
            improved_target = True
        if k != cat and dv is not None and dv < -tol:
            regressions.append((k, dv))
    print(f"aggregate: {before.get('aggregate')} -> {after.get('aggregate')}")
    if not improved_target:
        print(f"  targeted category '{cat}' did NOT improve.")
    if regressions:
        print("  regressions beyond tolerance: " + ", ".join(f"{k}({d:+.4f})" for k, d in regressions))
    verdict = "ACCEPT" if (improved_target and not regressions) else "REVERT"
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "ACCEPT" else 1


def main():
    ap = argparse.ArgumentParser(description="Alfred deterministic eval scorer + regression gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--suite", required=True)
    s.add_argument("--checks")
    s.add_argument("--responses")
    s.add_argument("--out")
    s.add_argument("--pass-threshold", dest="pass_threshold", type=float, default=0.75)
    g = sub.add_parser("gate")
    g.add_argument("--before", required=True)
    g.add_argument("--after", required=True)
    g.add_argument("--category", required=True)
    g.add_argument("--tolerance", type=float, default=0.02)
    args = ap.parse_args()
    if args.cmd == "score":
        sys.exit(do_score(args))
    if args.cmd == "gate":
        sys.exit(do_gate(args))


if __name__ == "__main__":
    main()

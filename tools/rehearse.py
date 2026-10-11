"""Rehearse an agent on our own case files: our scripts, checks and expected ledgers on the practice bank's PUB
customers, with an optional `now` per case.

    python tools/rehearse.py <agent_id> --cases tests/cases/pub01-friday.json [--reps 3] [--only ID,ID] [--style S]
                             [--out results/rehearse.json]

A case is a public-test-set case with these differences:
- `base`: the PUB case whose practice customer it uses (identity answers, products, customer id). Its `identity` is
  taken from the public set unless the case gives one.
- `variables`: merged over the practice session's variables, so `{"now": "2026-10-16T11:00"}` moves the call to
  another day; names and title come from the session.
- `expected` and `checks` are ours: the expected ledger is scored with the kit's tools/score.py, the checks go to the
  judge as success criteria.
A case passes only when every repetition passes. Nothing here is imported by the builder.
"""
import argparse, hashlib, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import freya
from score import http, score_ledger
from run_cases import STYLES, persona_for

PUBLIC = ROOT / "public-test-set.json"
STORE = ROOT / ".personas.json"


def load_cases(path, only=None):
    pub = {c["id"]: c for c in json.loads(PUBLIC.read_text(encoding="utf-8"))}
    out = []
    for c in json.loads(pathlib.Path(path).read_text(encoding="utf-8")):
        if only and c["id"] not in only:
            continue
        base = pub[c["base"]]
        case = dict(c)
        case.setdefault("identity", base["identity"])
        for k in ("brief", "family", "level", "title"):
            case.setdefault(k, base[k])
        case["variables"] = dict(base["variables"], **(c.get("variables") or {}))
        out.append(case)
    return out


def personas(cases, style):
    ids = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}
    spec = {c["id"]: persona_for(c, {"identity": c["identity"]}, style) for c in cases}
    key = {i: "R:" + i + ":" + hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10] for i, p in spec.items()}
    new = [c for c in cases if key[c["id"]] not in ids]
    if new:
        made = freya.create_personas([spec[c["id"]] for c in new])["personas"]
        ids.update({key[c["id"]]: p["id"] for c, p in zip(new, made)})
        STORE.write_text(json.dumps(ids, indent=1), encoding="utf-8")
    return {c["id"]: ids[key[c["id"]]] for c in cases}


def run_once(agent_id, cases, ids):
    sessions = {}
    for c in cases:
        v = http("/v1/practice/sessions", {"case": c["base"]})["variables"]
        sessions[c["id"]] = dict(v, **{k: x for k, x in (c.get("variables") or {}).items() if k == "now"})
    t0 = time.time()
    sim_id, it_id, status = freya.run_simulation(agent_id, f"rehearse {time.strftime('%H:%M:%S')}",
                                                 {ids[c["id"]]: sessions[c["id"]] for c in cases})
    took = time.time() - t0
    judge = {r["personaId"]: r for r in freya.get_iteration(sim_id, it_id)["runs"]}
    rows = []
    for c in cases:
        led = http("/v1/practice/ledger/" + sessions[c["id"]]["customer_id"])
        r = score_ledger(c, led)
        j = judge.get(ids[c["id"]], {})
        if j.get("outcome") != "pass":
            r["failed"].append("conversation: " + " ".join(str(j.get("reason") or "no verdict").split())[:400])
        r["pass"] = not r["failed"]
        rows.append({"id": c["id"], "base": c["base"], "now": sessions[c["id"]].get("now"), "pass": r["pass"],
                     "failed": r["failed"], "call_id": j.get("callId"), "tools": [x["tool"] for x in led["calls"]],
                     "customer_id": sessions[c["id"]]["customer_id"]})
        print(("PASS" if r["pass"] else "FAIL"), c["id"], f"({c['base']}, now {sessions[c['id']].get('now')})", "|",
              " > ".join(x["tool"] for x in led["calls"]) or "(no tool calls)")
        for f in r["failed"]:
            print("       -", f[:260])
    return rows, sim_id, status, took


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_id")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--only")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--style", choices=sorted(STYLES))
    ap.add_argument("--out")
    a = ap.parse_args()
    cases = load_cases(a.cases, a.only.split(",") if a.only else None)
    if not cases:
        sys.exit("no cases")
    ids = personas(cases, a.style)
    reps, total = [], 0.0
    for n in range(a.reps):
        rows, sim_id, status, took = run_once(a.agent_id, cases, ids)
        reps.append(rows)
        total += took
        if a.reps > 1:
            print(f"-- run {n + 1} of {a.reps}: {sum(r['pass'] for r in rows)} / {len(rows)} passed, {took:.0f} s\n")
    final = [{"id": c["id"], "passes": sum(r[k]["pass"] for r in reps), "runs": a.reps,
              "pass": all(r[k]["pass"] for r in reps)} for k, c in enumerate(cases)]
    if a.reps > 1:
        for f in final:
            print(("PASS" if f["pass"] else "FAIL"), f["id"], f"{f['passes']}/{f['runs']}")
    passed = sum(f["pass"] for f in final)
    print(f"\n{passed} / {len(final)} passed. Last simulation {sim_id}, status {status}, {total:.0f} s.")
    if a.out:
        pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out).write_text(json.dumps({"agent_id": a.agent_id, "cases": a.cases, "passed": passed,
                                                   "total": len(final), "final": final, "all_rows": reps}, indent=1),
                                       encoding="utf-8")


if __name__ == "__main__":
    main()

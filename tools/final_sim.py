"""Freya simulation of the final agent on the case's example scenarios (+ extra edge customers). Test tool only;
nothing here is used by the builder.

    python tools/final_sim.py <agent_id> <example-scenarios.md> [--extra extra.json] [--only 1,3] [--out results/x.json]
"""
import argparse, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya  # noqa: E402

PERSONA = ("You are a Turkish bank customer calling Freya Bank's phone line about your credit card. Speak ONLY Turkish, "
           "like a real person on the phone: short turns, natural wording. Do not reveal you are a simulation. "
           "Play exactly this situation and react to what the assistant says:\n\n{situation}\n\n"
           "End the call when the assistant routes you to the main menu or a human agent, or says goodbye.")


def parse(md):
    out = []
    for block in re.split(r"\n## ", md)[1:]:
        title = block.splitlines()[0].strip()
        num = re.match(r"Example (\d+)", title)
        cust = re.search(r"\*\*Customer number:\*\*\s*`(\d+)`", block)
        now = re.search(r"\*\*now:\*\*\s*`([^`]+)`", block)
        sit = re.search(r"\*\*Situation:\*\*(.*?)\*\*Expected behaviour:\*\*", block, re.S)
        exp = re.search(r"\*\*Expected behaviour:\*\*(.*?)(\n---|\Z)", block, re.S)
        if not (num and cust and now and sit and exp):
            continue
        out.append({"id": num.group(1), "title": title, "customerNumber": cust.group(1), "now": now.group(1),
                    "situation": sit.group(1).strip(), "expected": exp.group(1).strip()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agent_id"); ap.add_argument("scenarios"); ap.add_argument("--extra"); ap.add_argument("--only")
    ap.add_argument("--out")
    a = ap.parse_args()
    cases = parse(pathlib.Path(a.scenarios).read_text(encoding="utf-8"))
    if a.extra:
        cases += json.loads(pathlib.Path(a.extra).read_text(encoding="utf-8"))
    if a.only:
        cases = [c for c in cases if c["id"] in a.only.split(",")]
    personas = [{"name": f"final {c['id']} {c['title'][:60]}", "prompt": PERSONA.format(situation=c["situation"]),
                 "successCriteria": "All of these are true:\n" + c["expected"], "tags": ["final"]} for c in cases]
    made = freya.create_personas(personas)["personas"]
    vbp = {p["id"]: {"customerNumber": c["customerNumber"], "now": c["now"]} for c, p in zip(cases, made)}
    sim_id, it_id, status = freya.run_simulation(a.agent_id, "final-sim", vbp, max_seconds=420)
    it = freya.get_iteration(sim_id, it_id)
    runs = it.get("runs") or []
    byp = {p["id"]: c for c, p in zip(cases, made)}
    rows = []
    for r in runs:
        c = byp.get(r.get("personaId"), {})
        rows.append({"id": c.get("id"), "title": c.get("title"), "callId": r.get("callId"),
                     "pass": r.get("outcome") == "pass", "reason": " ".join(str(r.get("reason") or "no verdict").split())[:600]})
    for x in sorted(rows, key=lambda x: str(x["id"])):
        print(f"{'PASS' if x['pass'] else 'FAIL'} {x['id']} {x['title']} call={x['callId']}\n    {x['reason'][:400]}")
    print(f"{sum(1 for x in rows if x['pass'])} / {len(rows)} passed (simulation {sim_id}, {status})")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps({"sim": sim_id, "rows": rows, "raw": runs}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

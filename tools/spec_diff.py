"""Compare two specs by their facts, not their wording.

    python tools/spec_diff.py out/m2/spec.json out/m1/spec.json [--api mocks/m1/openapi.json]

Templates and ids are ignored. Expressions (selectors, deadlines, floor, first ask, ladder, callback day rule) are
compared by their values on a grid of days (around every holiday of both specs, Fridays, weekends) and products of
every kind and band. Events, situations and failure targets are compared by the codes they lead to; free-mode rules
by their stages (their triggers are free text); forbidden codes only when the tool API could carry them; an event's
persuasion suppression only when the event does not end the call. Prints one line per difference and exits 1 when
there is any.
"""
import argparse, datetime, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from builder import exprs, toolapi  # noqa: E402
from builder import policy_lib as pl  # noqa: E402
from builder import spec as sp  # noqa: E402

NS = "not_specified"


def get(d, path, default=None):
    found, v = sp.resolve(d, path)
    return v if found else default


def sit(spec, sid):
    s = next((x for x in spec.get("situations") or [] if x.get("id") == sid), None)
    if s is None:
        return sid if sid in (None, "", NS, "continue") else "?" + str(sid)
    return (s.get("outcome"), s.get("reason") or None, s.get("queue") or None, s.get("note_reason") or None)


def grid(spec_a, spec_b):
    hol = set((spec_a.get("calendar") or {}).get("holidays") or []) | set((spec_b.get("calendar") or {}).get("holidays") or [])
    anchors = {datetime.date(2026, 10, 14)} | {datetime.date.fromisoformat(h) for h in hol if h[:4] in ("2026", "2027")}
    days = sorted({a + datetime.timedelta(days=k) for a in anchors for k in range(-6, 7)})
    return [datetime.datetime.combine(d, datetime.time(11, 0)) for d in days]


def products(spec, api):
    out = []
    for p in exprs.synthetic_products(spec, api):
        if p["due_amount"] in (420.0, 12345.67) and p["days_overdue"] in (1, 3, 7, 8, 10, 11, 15, 30, 45, 60):
            out.append(p)
    return out


def values(spec, api, pattern, nows, prods):
    """{input key: value} of every expression matching pattern (list items by index)."""
    fs = exprs.compile_all(spec)
    cal = pl.calendar((spec.get("calendar") or {}).get("business_weekdays"), (spec.get("calendar") or {}).get("holidays"))
    h = pl.helpers(cal)
    out = {}
    for path, f in fs.items():
        if not _match(path, pattern):
            continue
        for now in nows:
            for p in prods or [None]:
                n = {"today": now.date(), "now": now}
                if p is not None:
                    n.update(p["raw"])
                    n.update({k: p[k] for k in ("ref", "kind", "due_amount", "days_overdue")})
                    n["floor"] = round(p["due_amount"] * 0.3, 2)
                    n["deadline"] = h["add_business_days"](now.date(), 7)
                    n["said_too_much"] = False
                for d in ([now.date() + datetime.timedelta(days=k) for k in range(0, 5)] if "d" in pattern else [None]):
                    if d is not None:
                        n["d"] = d
                    try:
                        v = f(n, h)
                    except Exception as e:  # noqa: BLE001
                        v = "raises " + type(e).__name__
                    key = (now.date().isoformat(), p and (p["kind"], p["days_overdue"]), d and d.isoformat())
                    out[key] = v.isoformat() if isinstance(v, (datetime.date, datetime.datetime)) else v
    return out


def _match(path, pattern):
    return sp.split_path(path)[:len(sp.split_path(pattern))] and all(
        a == b or b == "*" for a, b in zip(sp.split_path(path), sp.split_path(pattern))) and \
        len(sp.split_path(path)) == len(sp.split_path(pattern))


def band_facts(spec, api, nows, prods):
    """Per synthetic product and day: (band attempts, action, deadline) of the first band that selects it."""
    fs = exprs.compile_all(spec)
    cal = pl.calendar((spec.get("calendar") or {}).get("business_weekdays"), (spec.get("calendar") or {}).get("holidays"))
    h = pl.helpers(cal)
    bands = (spec.get("products") or {}).get("bands") or []
    out = {}
    for now in nows:
        for p in prods:
            n = {"today": now.date(), "now": now}
            n.update(p["raw"])
            n.update({k: p[k] for k in ("ref", "kind", "due_amount", "days_overdue")})
            got = None
            for i, b in enumerate(bands):
                try:
                    if fs[f"products.bands[{i}].selector"](n, h):
                        dl = fs.get(f"products.bands[{i}].deadline")
                        got = (b.get("attempts"), b.get("action"), dl(n, h).isoformat() if dl else None,
                               [s.get("step") for s in (spec.get("negotiation") or {}).get("ladder") or []
                                if not s.get("bands") or b.get("id") in s["bands"]])
                        break
                except Exception as e:  # noqa: BLE001
                    got = "raises " + type(e).__name__
                    break
            out[(now.date().isoformat(), p["kind"], p["days_overdue"])] = got
    return out


def facts(spec, api, nows, prods):
    f = {}
    cal = spec.get("calendar") or {}
    f["calendar.business_weekdays"] = cal.get("business_weekdays")
    f["calendar.holidays"] = sorted(cal.get("holidays") or [])
    f["calendar.weekday_semantics"] = cal.get("weekday_semantics")
    f["calendar.named_periods"] = sorted(p.get("phrase") for p in cal.get("named_periods") or [])
    oc = get(spec, "opening.confirmation", {}) or {}
    f["opening.max_asks"] = oc.get("max_asks")
    f["opening.question_then_reask_counts"] = oc.get("question_then_reask_counts")
    f["opening.on_unclear"] = sit(spec, oc.get("on_unclear"))
    cb = get(spec, "third_party.callback", {}) or {}
    for k in ("enabled", "windows", "start_inclusive", "end_inclusive", "after_now", "min_lead_minutes",
              "granularity_minutes", "retry_asks"):
        f["callback." + k] = cb.get(k)
    f["callback.on_booked"] = sit(spec, cb.get("on_booked"))
    f["callback.on_invalid_exhausted"] = sit(spec, cb.get("on_invalid_exhausted"))
    f["callback.day_rule"] = values(spec, api, "third_party.callback.day_rule", nows, None)
    f["third_party.terminations"] = sorted(str(sit(spec, t.get("situation"))) for t in get(spec, "third_party.terminations", []) or [])
    idt = spec.get("identity") or {}
    f["identity.questions"] = [(q.get("field"), q.get("answer_type")) for q in idt.get("questions") or []]
    f["identity.limits"] = {k: v for k, v in (idt.get("limits") or {}).items() if k != "refs"}
    f["identity.hints"] = sorted((h.get("value"), h.get("counts_as_wrong")) for h in idt.get("hints") or [])
    dec = idt.get("decline") or {}
    f["identity.decline"] = (dec.get("max_refusals"), dec.get("purpose_question"), sit(spec, dec.get("on_declined")))
    f["identity.on_failed"] = sit(spec, idt.get("on_failed"))
    f["products.bands"] = band_facts(spec, api, nows, prods)
    f["products.floor"] = values(spec, api, "products.floor", nows[:1], prods)
    f["products.unmatched"] = sit(spec, get(spec, "products.unmatched"))
    neg = spec.get("negotiation") or {}
    f["first_ask.amount"] = values(spec, api, "negotiation.first_ask.amount", nows, prods)
    f["first_ask.date"] = values(spec, api, "negotiation.first_ask.date", nows, prods)
    f["first_ask.scope"] = get(spec, "negotiation.first_ask.scope")
    for k in ("non_business_day.policy", "after_deadline.policy", "vague.re_asks", "vague.then", "non_answer_is_refusal",
              "dedupe_refused_offers", "amounts.accept_range", "amounts.too_much.policy", "amounts.below_floor.policy",
              "amounts.floor_offers", "multi.overflow", "multi.failed_promise"):
        f["negotiation." + k] = get(neg, k)
    f["negotiation.on_exhausted"] = sit(spec, neg.get("on_exhausted"))
    for i, _ in enumerate(neg.get("ladder") or []):
        f[f"ladder[{i}].amount"] = values(spec, api, f"negotiation.ladder[{i}].amount", nows, prods)
        f[f"ladder[{i}].date"] = values(spec, api, f"negotiation.ladder[{i}].date", nows, prods)
    f["confirm.on_recorded"] = sit(spec, get(spec, "confirm.on_recorded"))
    for role in ("lookup", "record_promise", "book_callback", "transfer"):
        fp = get(spec, "failure_policy." + role, {}) or {}
        f["failure." + role] = (fp.get("retries"), sit(spec, fp.get("on_exhausted")))
    evs = []
    for e in spec.get("events") or []:
        asks = any(e.get(k) not in (None, NS) for k in ("on_yes", "on_no"))
        ends = isinstance(sit(spec, e.get("then")), tuple) and not asks
        evs.append((tuple(sorted(e.get("stages") or [])), e.get("min_occurrences"),
                    bool(e.get("below_min_template") not in (None, NS)),
                    "persuasion" in (e.get("suppresses") or []) and not ends,  # moot when the event ends the call
                    str(sit(spec, e.get("on_yes"))), str(sit(spec, e.get("on_no"))), str(sit(spec, e.get("then"))),
                    str(sit(spec, e.get("then_before_verification"))), e.get("several_products")))
    f["events"] = sorted(evs)
    f["side_questions"] = (len(get(spec, "side_questions.answers", []) or []), get(spec, "side_questions.other.policy"))
    f["notes"] = (get(spec, "notes.enabled"), get(spec, "notes.on_transfer"))
    f["closing.aggregation"] = sorted(get(spec, "closing.aggregation.skip_note_reasons", []) or [])
    f["closing.transfer_overrides_promise"] = get(spec, "closing.transfer_overrides_promise")
    f["closing.hangup"] = {k: sit(spec, (v or {}).get("situation")) for k, v in (get(spec, "closing.hangup", {}) or {}).items()}
    f["situations"] = sorted(str(sit(spec, s.get("id"))) for s in spec.get("situations") or [])
    sendable = {v for t in api.tools.values() for fl in t.request.values() for v in (fl.enum or [])}
    f["forbidden_codes"] = sorted(c for c in spec.get("forbidden_codes") or [] if c in sendable)  # others cannot be sent
    f["free_mode_rules"] = sorted(r.get("stage") for r in spec.get("residual_rules") or [] if r.get("free_mode") is True)
    return f


def diff(a, b, api):
    nows = grid(a, b)
    prods = products(a, api)
    fa, fb = facts(a, api, nows, prods), facts(b, api, nows, prods)
    out = []
    for k in fa:
        if fa[k] != fb.get(k):
            va, vb = fa[k], fb.get(k)
            if isinstance(va, list) and isinstance(vb, list) and len(va) == len(vb) and len(va) > 1:
                pairs = [(x, y) for x, y in zip(va, vb) if x != y]
                out.append(f"{k}: {len(pairs)} item(s) differ: " + "; ".join(f"{x!r} vs {y!r}" for x, y in pairs[:3]))
            elif isinstance(va, dict) and isinstance(vb, dict):
                keys = [x for x in sorted(set(va) | set(vb), key=str) if va.get(x) != vb.get(x)]
                out.append(f"{k}: {len(keys)} input(s) differ, e.g. {keys[0]}: {va.get(keys[0])!r} vs {vb.get(keys[0])!r}")
            else:
                out.append(f"{k}: {va!r} vs {vb!r}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--api", default=str(ROOT / "tools" / "openapi.json"))
    x = ap.parse_args()
    a = json.loads(pathlib.Path(x.a).read_text(encoding="utf-8"))
    b = json.loads(pathlib.Path(x.b).read_text(encoding="utf-8"))
    api = toolapi.parse(json.loads(pathlib.Path(x.api).read_text(encoding="utf-8")))
    out = diff(a, b, api)
    for line in out:
        print(line)
    print(f"{len(out)} difference(s)")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())

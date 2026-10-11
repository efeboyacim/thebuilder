"""Talk to a saved phone agent turn by turn, with you playing the customer.

    python3 tools/chat.py <workflow_id> "Yes, speaking." "It is 14 March 1988." ... [--now 2026-10-14T11:00]
        [--first Daniel --last Reyes --title Mr] [--name what-this-tests]

Every message is one customer turn in the same call; the agent's reply to each is printed. The call uses a made-up
customer id, so the bank knows no such customer: identity checks fail and nothing is ever written to the bank. Test
what happens before and during the security questions (the opening, someone else answering, refusals, questions,
wrong answers), not promises. Each conversation is appended to chat_log.jsonl.
"""
import argparse, json, pathlib, sys, time, uuid

import freya

LOG = pathlib.Path("chat_log.jsonl")


def reply_to(cid, n):
    """The agent's words after the n-th customer message, read from the call (for a turn the route timed out on)."""
    try:
        msgs = freya.get_call(cid).get("messages") or []
    except Exception:
        return None
    seen, words = 0, []
    for m in msgs:
        if m.get("role") == "user":
            seen += 1
            if seen > n:
                break
            words = []
        elif m.get("role") in ("assistant", "bot") and seen == n and m.get("message"):
            words.append(m["message"])
    return " ".join(words) if words else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow_id")
    ap.add_argument("messages", nargs="+")
    ap.add_argument("--now", default=time.strftime("%Y-%m-%dT%H:%M"))
    ap.add_argument("--first", default="Alex")
    ap.add_argument("--last", default="Morgan")
    ap.add_argument("--title", default="Mx")
    ap.add_argument("--name", default="")
    a = ap.parse_args()
    cv = {"customer_id": "c_selftest_" + uuid.uuid4().hex[:8], "first_name": a.first, "last_name": a.last,
          "title": a.title, "now": a.now}
    cid, rows = None, []
    for n, msg in enumerate(a.messages, 1):
        body = {"workflowId": a.workflow_id, "message": msg}
        body.update({"callId": cid} if cid else {"customerVariables": cv})
        t = time.time()
        try:
            r = freya.api("POST", "/api/v2/chat/message", body)
            cid = cid or r.get("callId")
            agent = r.get("response", "")
        except Exception as e:  # a long turn returns 504 while the engine finishes it
            agent = None
            if cid and "504" in str(e):
                while time.time() - t < 120 and agent is None:
                    time.sleep(5)
                    agent = reply_to(cid, n)
            if agent is None:
                agent = f"[error: {str(e)[:160]}]"
        rows.append({"customer": msg, "agent": agent, "seconds": round(time.time() - t, 1)})
        print(f"customer: {msg}\nagent:    {agent}\n", flush=True)
    if cid:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": cid})
        except Exception:
            pass
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"name": a.name, "workflow_id": a.workflow_id, "call_id": cid, "variables": cv,
                            "turns": rows}) + "\n")


if __name__ == "__main__":
    sys.exit(main())

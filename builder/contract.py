"""The extraction contract: the fields each talk node extracts for the brain, generated from the spec.

The compiler builds every talk node's extraction schema from `extraction(spec)`; the local replay checks its acts
against it. The brain (builder/engine.py) reads these fields as x_<slot>. Builder side only: not part of the brain.
"""
from builder.engine import SIDE_Q, STAGES, x_var

MAIN = dict(open=("confirmed", "someone_else", "unclear"),
            third=("gives_time", "no_time", "offers_to_pay", "customer_available", "unclear"),
            identity=("answers", "refuses", "unclear"),
            negotiate=("accepts", "refuses", "too_much", "proposes", "unclear"),
            confirm=("yes", "no", "change", "unclear"))
STAGE_SLOTS = dict(open=(), third=("day", "time"), identity=("answer",), negotiate=("day",), confirm=("day",))
DAY_TEXT = dict(
    third="The day named in the LAST message exactly as said (for example a weekday, tomorrow, or a date); none if "
          "absent.",
    negotiate="The day or period the person offers or agrees to pay on in the LAST message, exactly as said, with "
              "every word that places it (a weekday with this or next, a date, a day of the month, tomorrow, a "
              "number of days, a week or a month, or an event such as a payday). none if absent, and none when the person only says they cannot pay on "
              "a day.",
    confirm="A different day the person now wants to pay on in the LAST message, exactly as said; none if absent or "
            "if they only repeat the day that was read back.")


def extraction(S):
    """{stage: [field]} with name, type, enum (or none) and description, generated from the spec."""
    conf = S["opening"]["confirmation"]
    terms = S["third_party"].get("terminations") or []
    who = "the person the call is for"
    meaning = dict(
        confirmed="the person confirms they are " + who + ". Counts: " + "; ".join(conf.get("counts_as_yes") or [])
                  + ". Does not count: " + "; ".join(conf.get("does_not_count") or []),
        someone_else="the person says they are not " + who + " (a relative, a colleague, someone else)",
        unclear="anything else, or no clear answer",
        gives_time="the person names a day or a time when " + who + " can be reached",
        no_time="the person does not know or will not say when " + who + " can be reached",
        offers_to_pay="the person offers to pay or to help instead",
        customer_available=who + " comes to the phone now",
        answers="the person gives an answer to the security question",
        refuses="the person does not want to answer or says no",
        accepts="the person agrees to the payment the agent proposed",
        too_much="the person says the amount is too much",
        proposes="the person proposes their own date, period or amount instead",
        yes="the person clearly confirms what was read back",
        no="the person does not confirm what was read back",
        change="the person wants to change the date or the amount")
    out = dict()
    for stage in STAGES:
        mains = list(MAIN[stage])
        desc = ["From the customer's LAST message only. " + v + ": " + meaning[v] + "." for v in mains]
        for t in terms:
            if stage in (t.get("stages") or []):
                mains.append(t["id"])
                desc.append(t["id"] + ": " + t.get("trigger", "") + ".")
        fields = [dict(name=x_var("main"), type="string", enum=mains + ["none"], description=" ".join(desc)),
                  dict(name=x_var("side_q"), type="string", enum=list(SIDE_Q) + ["none"],
                       description="A question the person asks in their LAST message: robot (whether the agent is a "
                                   "robot or an AI), scam (whether the call is a scam), purpose (what the call is "
                                   "about), other (any other question), none."),
                  dict(name=x_var("said"), type="string", enum=None,
                       description="The person's LAST message, word for word.")]
        if "answer" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("answer"), type="string", enum=None, description=(
                "The answer to the security question in the LAST message exactly as said, in the person's own "
                "words and numbers; do not reformat it. none if absent.")))
        if "day" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("day"), type="string", enum=None, description=DAY_TEXT[stage]))
        if "time" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("time"), type="string", enum=None, description=(
                "The clock time named in the LAST message exactly as said; none if absent.")))
        out[stage] = fields
    return out

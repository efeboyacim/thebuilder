"""The extraction contract: the fields each talk node extracts for the brain, generated from the spec.

The compiler builds every talk node's extraction schema from `extraction(spec)`; the local replay checks its acts
against it. The brain (builder/engine.py) reads these fields as x_<slot>. Builder side only: not part of the brain.
"""
from builder.engine import FREE, STAGES, x_var

MAIN = dict(open=("confirmed", "someone_else", "unclear"),
            third=("gives_time", "no_time", "offers_to_pay", "customer_available", "unclear"),
            identity=("answers", "refuses", "unclear"),
            negotiate=("accepts", "refuses", "too_much", "proposes", "unclear"),
            confirm=("yes", "no", "change", "unclear"))
STAGE_SLOTS = dict(open=(), third=("day", "time"), identity=("answer",), negotiate=("day", "amount"),
                   confirm=("day", "amount"))
DAY_TEXT = dict(
    third="The day named in the LAST message exactly as said (for example a weekday, tomorrow, or a date); none if "
          "absent.",
    negotiate="The day or period the person offers or agrees to pay on in the LAST message, exactly as said, with "
              "every word that places it (a weekday with this or next, a date, a day of the month, tomorrow, a "
              "number of days, a week or a month, or an event such as a payday). none if absent, none when the "
              "person only says they cannot pay on a day, and none when they only accept the day the agent named.",
    confirm="A different day the person now wants to pay on in the LAST message, exactly as said; none if absent or "
            "if they only repeat the day that was read back.")
AMOUNT_TEXT = ("An amount of money the person themselves offers to pay in the LAST message, exactly as said, in "
               "digits or words (or words such as the minimum, the full amount); none if absent, and none when they "
               "only repeat or refuse the amount the agent named.")
SCOPE_TEXT = ("Whether what the person says in their LAST message is about all their products together (all), about "
              "one product (one), or neither (none).")
PRODUCT_TEXT = ("The product the person talks about in their LAST message, in their own words (its kind, a name or a "
                "number); none if they name none.")


def several(S):
    """True when the spec handles customers with several products."""
    fa = S["negotiation"]["first_ask"]
    return fa.get("scope") in ("all_products", "each_product") or fa.get("template_multi") not in (None, "", "not_specified")


REPLY_TEXT = ("The person's answer to a yes/no question the agent asked just before: yes, no, or none when the LAST "
              "message does not answer it.")


def extraction(S):
    """{stage: [field]} with name, type, enum (or none) and description, generated from the spec."""
    conf = S["opening"]["confirmation"]
    terms = S["third_party"].get("terminations") or []
    events = S.get("events") or []
    answers = (S.get("side_questions") or {}).get("answers") or []
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
    side_desc = ("A question the person asks in their LAST message: " +
                 "".join(a["id"] + " (" + a.get("trigger", "") + "), " for a in answers) +
                 "purpose (what the call is about), other (any other question), none.")
    out = dict()
    for stage in STAGES:
        mains = list(MAIN[stage])
        desc = ["From the customer's LAST message only. " + v + ": " + meaning[v] + "." for v in mains]
        for t in terms:
            if stage in (t.get("stages") or []):
                mains.append(t["id"])
                desc.append(t["id"] + ": " + t.get("trigger", "") + ".")
        fields = [dict(name=x_var("main"), type="string", enum=mains + ["none"], description=" ".join(desc)),
                  dict(name=x_var("side_q"), type="string", enum=[a["id"] for a in answers] + ["purpose", "other", "none"],
                       description=side_desc),
                  dict(name=x_var("said"), type="string", enum=None,
                       description="The person's LAST message, word for word.")]
        evs = [e for e in events if stage in (e.get("stages") or [])] + \
            [dict(id=r["id"], trigger=r["trigger"]) for r in free(S, stage)]
        if evs:
            fields.append(dict(name=x_var("event"), type="string", enum=[e["id"] for e in evs] + ["none"],
                               description="What the person says in their LAST message, if it is one of these "
                                           "(when several fit, the first listed): " +
                                           " ".join(e["id"] + ": " + e.get("trigger", "") + "." for e in evs) +
                                           " none: none of these. Label one only when the person's own words in the "
                                           "LAST message clearly state it; a question, an answer to the agent's "
                                           "offer, or a remark that the amount is high is none unless it states "
                                           "it. When in doubt: none."))
            fields.append(dict(name=x_var("reply"), type="string", enum=["yes", "no", "none"], description=REPLY_TEXT))
        if "answer" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("answer"), type="string", enum=None, description=(
                "The answer to the security question in the LAST message exactly as said, in the person's own "
                "words and numbers; do not reformat it. none if absent.")))
        if "day" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("day"), type="string", enum=None, description=DAY_TEXT[stage]))
        if "time" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("time"), type="string", enum=None, description=(
                "The clock time named in the LAST message exactly as said; none if absent.")))
        if "amount" in STAGE_SLOTS[stage]:
            fields.append(dict(name=x_var("amount"), type="string", enum=None, description=AMOUNT_TEXT))
        if several(S) and stage in ("negotiate", "confirm"):
            fields.append(dict(name=x_var("scope"), type="string", enum=["all", "one", "none"], description=SCOPE_TEXT))
            fields.append(dict(name=x_var("product"), type="string", enum=None, description=PRODUCT_TEXT))
        out[stage] = fields
    for stage in STAGES:
        if free(S, stage):
            out[FREE + stage] = free_fields()
    return out


def free(S, stage):
    from builder.brain import free_rules
    return [r for r in free_rules(S) if r["stage"] == stage]


def free_fields():
    return [
        dict(name=x_var("free_action"), type="string",
             enum=["promise", "callback", "close", "transfer", "done", "continue", "none"],
             description="What the conversation has now settled, judged from the agent's last message and the person's "
                         "LAST message: promise (the person accepts a payment the agent proposed), callback (the person "
                         "accepts a callback day and time the agent proposed), close or transfer (the agent said the call "
                         "ends that way), done (the special rule no longer applies, or the person wants to go back), "
                         "continue (still being discussed), none."),
        dict(name=x_var("free_product"), type="string", enum=None,
             description="For promise: copy from the AGENT's last message the product of the payment the agent proposed "
                         "(its id such as p1, or its name). The person agreeing ('yes', 'I confirm') is enough; they "
                         "need not repeat it."),
        dict(name=x_var("free_amount"), type="string", enum=None,
             description="For promise: copy from the AGENT's last message the amount of the payment the agent proposed, "
                         "as digits. The person agreeing is enough; when the person states the amount themselves, use theirs."),
        dict(name=x_var("free_date"), type="string", enum=None,
             description="For promise or callback: copy from the AGENT's last message the date the agent proposed, as "
                         "YYYY-MM-DD (the agent says the year). The person agreeing is enough; when the person states the "
                         "date themselves, use theirs."),
        dict(name=x_var("free_time"), type="string", enum=None, description="For callback: the time as HH:MM."),
        dict(name=x_var("free_target"), type="string", enum=None,
             description="For close or transfer: the outcome or queue the agent named."),
        dict(name=x_var("said"), type="string", enum=None, description="The person's LAST message, word for word.")]

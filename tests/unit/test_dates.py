"""Dates and the persuasion ladder (item 11): calendar helpers, day phrases resolved against `now`, bands and
deadlines, holiday and weekend shifts, vague periods, the after-deadline offer, the ladder with its refused-offer
record, and the promise retry. Calls run on several days: Christmas and New Year weeks, Fridays, weekends and
after-hours clocks.

The engine scenarios run the Brief A fixture spec through the brain code string and the stub bank, scored with the
kit's tools/score.py like test_engine_slice.py.
"""
import copy, datetime

import pytest

from builder import brain, policy_lib as pl, toolapi
from tests.helpers import FIX, ROOT, load, mock_ledger
from tests.unit.test_engine_slice import CASES, expect, play, score

D = datetime.date
HOLIDAYS = ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"]
CAL = pl.calendar(["mon", "tue", "wed", "thu", "fri"], HOLIDAYS)


@pytest.fixture(scope="module")
def api():
    return toolapi.parse(load(ROOT / "tools" / "openapi.json"))


@pytest.fixture(scope="module")
def spec():
    return load(FIX / "spec-a-v1.json")


@pytest.fixture(scope="module")
def code(spec, api):
    return brain.source(spec, api)


def verified(case_id):
    ids = CASES[case_id]["identity"]
    return [dict(stage="open", main="confirmed", said="Yes, speaking."),
            dict(stage="identity", main="answers", answer=ids["date_of_birth"]),
            dict(stage="identity", main="answers", answer=ids["phone_last4"])]


def promise(ref, amount, date):
    return {"product_id": ref, "amount": amount, "date": date}


def offers(r):
    """What the agent said after the disclosure."""
    texts = r.agent_texts()
    i = next(n for n, t in enumerate(texts) if "collections" in t)
    return texts[i:]


# ---------- calendar helpers ----------

@pytest.mark.parametrize("start,n,want", [
    ("2026-12-21", 7, "2026-12-31"), ("2026-12-21", 12, "2027-01-08"),    # Christmas week
    ("2026-12-24", 1, "2026-12-28"), ("2026-12-31", 1, "2027-01-04"),    # over Christmas and New Year
    ("2026-10-16", 7, "2026-10-28"), ("2026-10-17", 7, "2026-10-28"),    # a Friday, a Saturday
    ("2026-10-14", 12, "2026-11-02"), ("2026-11-25", 1, "2026-11-27"),
])
def test_add_business_days(start, n, want):
    assert pl.add_business_days(CAL, D.fromisoformat(start), n) == D.fromisoformat(want)


@pytest.mark.parametrize("day,offset,want", [
    ("2026-10-14", 0, "2026-10-16"), ("2026-10-16", 1, "2026-10-23"),
    ("2026-12-21", 0, "2026-12-24"), ("2026-12-28", 0, "2026-12-31"),   # Friday holidays
    ("2026-12-24", 1, "2026-12-31"), ("2026-10-18", 0, "2026-10-16"),   # Sunday: the week's Friday
])
def test_last_business_day_of_week(day, offset, want):
    assert pl.last_bd_of_week(CAL, D.fromisoformat(day), offset) == D.fromisoformat(want)


def test_roll_skips_weekends_and_holidays():
    assert pl.roll(CAL, D(2026, 12, 25), "next") == D(2026, 12, 28)
    assert pl.roll(CAL, D(2027, 1, 1), "prev") == D(2026, 12, 31)
    assert pl.roll(CAL, D(2026, 10, 24), "next") == D(2026, 10, 27)


# ---------- day phrases ----------

WED = D(2026, 10, 14)
FRI = D(2026, 10, 16)
SAT = D(2026, 10, 17)
XMAS_EVE = D(2026, 12, 24)
NY_WEEK = D(2026, 12, 28)


@pytest.mark.parametrize("text,today,want", [
    ("next Friday", WED, "2026-10-23"), ("this Friday", WED, "2026-10-16"), ("Friday", WED, "2026-10-16"),
    ("next Friday", FRI, "2026-10-23"), ("this Friday", FRI, "2026-10-16"), ("Friday", SAT, "2026-10-23"),
    ("this Friday", SAT, "2026-10-16"),                     # passed: the engine asks for another day
    ("Monday next week", WED, "2026-10-19"), ("Friday the week after next", WED, "2026-10-30"),
    ("Monday the 26th", WED, "2026-10-26"), ("the 26th", WED, "2026-10-26"), ("on the 3rd", WED, "2026-11-03"),
    ("salary comes on 6 November", WED, "2026-11-06"), ("the twenty-seventh of October", WED, "2026-10-27"),
    ("tomorrow", XMAS_EVE, "2026-12-25"), ("next Friday", XMAS_EVE, "2027-01-01"),
    ("Monday the 4th", NY_WEEK, "2027-01-04"), ("in three days", NY_WEEK, "2026-12-31"),
    ("today", SAT, "2026-10-17"), ("2026-10-27", WED, "2026-10-27"), ("27/10", WED, "2026-10-27"),
])
def test_resolve_day_calendar_week(text, today, want):
    assert pl.resolve_day(text, today, "calendar_week") == D.fromisoformat(want)


@pytest.mark.parametrize("text", ["next week", "the week after next", "end of the month", "when my salary comes",
                                  "in a few days", "at the weekend", "in two weeks", "sometime next month"])
def test_vague_periods_name_no_day(text):
    assert pl.resolve_day(text, WED, "calendar_week") is None


def test_nearest_mode_reads_this_and_next_as_the_coming_day():
    assert pl.resolve_day("next Friday", WED, "nearest") == D(2026, 10, 16)
    assert pl.resolve_day("this Monday", WED, "nearest") == D(2026, 10, 19)
    assert pl.resolve_day("next Friday", FRI, "nearest") == D(2026, 10, 23)


# ---------- bands and deadlines on several days ----------

def deadline_after_lookup(spec, api, code, case_id, now):
    r = play(spec, api, code, case_id, verified(case_id), now=now)  # hangs up at the first ask
    return r.state()["products"][0]


@pytest.mark.parametrize("now,early,late", [
    ("2026-10-14T11:00", "2026-11-02", "2026-10-23"),
    ("2026-10-16T18:59", "2026-11-04", "2026-10-28"),     # Friday, before the holiday week
    ("2026-10-17T10:00", "2026-11-04", "2026-10-28"),     # Saturday
    ("2026-11-25T21:45", "2026-12-14", "2026-12-07"),     # after hours, the day before a holiday
    ("2026-12-21T11:00", "2027-01-08", "2026-12-31"),     # Christmas week
    ("2026-12-24T21:45", "2027-01-13", "2027-01-06"),     # Christmas Eve, after hours
    ("2026-12-28T07:30", "2027-01-14", "2027-01-07"),     # New Year week, early morning
])
def test_band_and_deadline_follow_now(spec, api, code, now, early, late):
    p7 = deadline_after_lookup(spec, api, code, "PUB-07", now)    # 5 days late
    p10 = deadline_after_lookup(spec, api, code, "PUB-10", now)   # 8 days late
    assert (p7["band"], p7["deadline"], p7["attempts"]) == ("early", early, 1)
    assert (p10["band"], p10["deadline"], p10["attempts"]) == ("late", late, 2)
    assert p10["floor"] == 109.5 and p7["floor"] == 180.0


# ---------- acceptance scripts ----------

def test_pub07_vague_period_then_a_holiday(spec, api, code):
    acts = verified("PUB-07") + [
        dict(stage="negotiate", main="refuses", said="No, I can't pay today."),
        dict(stage="negotiate", main="proposes", day="the week after next", said="Maybe the week after next."),
        dict(stage="negotiate", main="proposes", day="Monday the 26th", said="Monday the 26th."),
        dict(stage="negotiate", main="accepts", said="Yes, Tuesday is fine."),
        dict(stage="confirm", main="yes", said="Yes.")]
    r = play(spec, api, code, "PUB-07", acts)
    res = score.score_ledger(CASES["PUB-07"], mock_ledger(r.stub))
    assert res["pass"], (res["failed"], r.agent_texts())
    said = offers(r)
    assert said[1] == "Could you pay 540 on Friday, 16 October?"
    assert said[2] == "Which exact day would you pay on?"
    assert said[3].startswith("Monday, 26 October is not a business day. The next business day is Tuesday, 27 October.")
    assert "Tuesday, 27 October" in said[4] and said[4].endswith("Do you confirm?")


def test_pub10_promise_retried_once_after_a_failure(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="refuses", said="Not today, no."),
                                 dict(stage="negotiate", main="accepts", said="Friday works, the full amount."),
                                 dict(stage="confirm", main="yes", said="Yes, I confirm.")]
    r = play(spec, api, code, "PUB-10", acts, faults={"record_promise": ["500"]})
    res = score.score_ledger(CASES["PUB-10"], mock_ledger(r.stub))
    assert res["pass"], (res["failed"], r.agent_texts())
    assert [c["role"] for c in r.stub.ledger["calls"]].count("record_promise") == 2
    assert "is recorded" in r.agent_texts()[-1]


@pytest.mark.parametrize("fault", ["500", "ok_false", "timeout"])
def test_a_promise_that_fails_twice_is_not_claimed(spec, api, code, fault):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-10", acts, faults={"record_promise": [fault, fault]})
    expect(r, "no_promise", "service_error")
    assert "could not record" in r.agent_texts()[-1] and "is recorded" not in r.agent_texts()[-1]
    assert [c["role"] for c in r.stub.ledger["calls"]].count("record_promise") == 2


def test_pub04_date_after_the_deadline_gets_the_deadline(spec, api, code):
    acts = verified("PUB-04") + [
        dict(stage="negotiate", main="proposes", day="6 November", said="My salary comes on 6 November."),
        dict(stage="negotiate", main="accepts", said="Fine, the 23rd then."),
        dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-04", acts)
    expect(r, "promise", promises=[promise("p1", 1250.0, "2026-10-23")])
    assert offers(r)[1] == ("The latest date I can record is Friday, 23 October. "
                            "Can you pay 1250 on Friday, 23 October?")


# ---------- the ladder ----------

def test_late_band_two_attempts_then_refused(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="refuses")] * 3
    r = play(spec, api, code, "PUB-10", acts)
    expect(r, "no_promise", "refused")
    said = offers(r)
    assert said[1] == "Could you pay 365 on Friday, 16 October?"
    assert said[2] == "Could you pay 109.50 on Friday, 23 October? The rest stays overdue."
    assert len(said) == 4  # disclosure, attempt 1, attempt 2, farewell


def test_early_band_one_attempt_then_refused(spec, api, code):
    acts = verified("PUB-07") + [dict(stage="negotiate", main="refuses")] * 2
    r = play(spec, api, code, "PUB-07", acts)
    expect(r, "no_promise", "refused")
    assert len(offers(r)) == 3


@pytest.mark.parametrize("now,first,second", [
    ("2026-10-16T11:00", "Friday, 23 October", "Wednesday, 28 October"),   # Friday: attempt 1 is next week's
    ("2026-12-24T21:45", "Thursday, 31 December", "Wednesday, 6 January 2027"),  # Christmas Eve is the week's last
    ("2026-12-21T11:00", "Thursday, 24 December", "Thursday, 31 December"),
])
def test_attempt_dates_follow_now(spec, api, code, now, first, second):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="refuses")] * 2
    said = offers(play(spec, api, code, "PUB-10", acts, now=now))
    assert said[1] == f"Could you pay 365 on {first}?"
    assert said[2] == f"Could you pay 109.50 on {second}? The rest stays overdue."


def test_a_non_answer_uses_an_attempt(spec, api, code):
    acts = verified("PUB-07") + [dict(stage="negotiate", main="unclear", said="Hmm."),
                                 dict(stage="negotiate", main="unclear", said="Well.")]
    expect(play(spec, api, code, "PUB-07", acts), "no_promise", "refused")


def test_a_side_question_uses_no_attempt(spec, api, code):
    acts = verified("PUB-07") + [dict(stage="negotiate", side_q="robot", said="Are you a robot?"),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-07", acts), "promise", promises=[promise("p1", 540.0, "2026-10-14")])


def test_a_refused_offer_is_not_made_again(spec, api, code):
    s = copy.deepcopy(spec)
    s["negotiation"]["ladder"][1]["amount"] = "due_amount"  # attempt 2 would repeat the refused deadline offer
    acts = verified("PUB-10") + [
        dict(stage="negotiate", main="proposes", day="6 November"),     # after the deadline: the deadline is offered
        dict(stage="negotiate", main="refuses"),                       # refused: attempt 1
        dict(stage="negotiate", main="refuses")]                       # attempt 2 would repeat: the end
    r = play(s, api, brain.source(s, api), "PUB-10", acts)
    expect(r, "no_promise", "refused")
    said = offers(r)
    assert said[1].startswith("The latest date I can record is Friday, 23 October.")
    assert said[2] == "Could you pay 365 on Friday, 16 October?"
    assert len(said) == 4


def test_refusing_the_read_back_uses_an_attempt(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="accepts"),
                                 dict(stage="confirm", main="no", said="No, actually not."),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-10", acts), "promise", promises=[promise("p1", 365.0, "2026-10-16")])


# ---------- dates the customer names ----------

def test_a_weekend_day_gets_the_next_business_day(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="proposes", day="Saturday"),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-10", acts), "promise", promises=[promise("p1", 365.0, "2026-10-19")])


def test_a_valid_day_goes_straight_to_the_read_back(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="refuses", day="Thursday", said="Not today, Thursday."),
                                 dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-10", acts)
    expect(r, "promise", promises=[promise("p1", 365.0, "2026-10-15")])


def test_saying_no_to_today_is_not_a_proposal_of_today(spec, api, code):
    acts = verified("PUB-07") + [dict(stage="negotiate", main="refuses", day="today", said="I can't pay today."),
                                 dict(stage="negotiate", main="refuses")]
    expect(play(spec, api, code, "PUB-07", acts), "no_promise", "refused")


def test_a_passed_day_is_asked_again(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="proposes", day="this Monday"),
                                 dict(stage="negotiate", main="proposes", day="Thursday"),
                                 dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-10", acts)
    expect(r, "promise", promises=[promise("p1", 365.0, "2026-10-15")])
    assert offers(r)[1] == ("I can record a business day from today until Friday, 23 October. "
                            "Which day would you pay on?")


def test_vague_twice_proposes_the_deadline(spec, api, code):
    acts = verified("PUB-07") + [dict(stage="negotiate", main="proposes", day="end of the month"),
                                 dict(stage="negotiate", main="proposes", said="When my salary comes."),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-07", acts)
    expect(r, "promise", promises=[promise("p1", 540.0, "2026-11-02")])
    assert offers(r)[2] == "Can you pay 540 on Monday, 2 November?"


def test_a_changed_day_at_the_read_back_is_checked_again(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="accepts"),
                                 dict(stage="confirm", main="change", day="Sunday", said="Make it Sunday."),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-10", acts), "promise", promises=[promise("p1", 365.0, "2026-10-19")])


def test_yes_with_the_read_back_day_records(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="proposes", day="Friday"),
                                 dict(stage="confirm", main="yes", day="Friday", said="Yes, Friday.")]
    expect(play(spec, api, code, "PUB-10", acts), "promise", promises=[promise("p1", 365.0, "2026-10-16")])


@pytest.mark.parametrize("now,want", [("2026-10-17T10:00", "2026-10-19"),     # Saturday: today is no business day
                                      ("2026-12-25T11:00", "2026-12-28")])    # a holiday
def test_paying_today_on_a_non_business_day_moves_to_the_next(spec, api, code, now, want):
    acts = verified("PUB-01") + [dict(stage="negotiate", main="accepts"), dict(stage="negotiate", main="accepts"),
                                 dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-01", acts, now=now), "promise", promises=[promise("p1", 420.0, want)])


def test_after_hours_today_is_still_today(spec, api, code):
    acts = verified("PUB-01") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-01", acts, now="2026-10-14T23:30"), "promise",
           promises=[promise("p1", 420.0, "2026-10-14")])


def test_christmas_week_next_friday_is_new_years_day(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="proposes", day="next Friday"),
                                 dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-10", acts, now="2026-12-24T11:00")
    expect(r, "promise", promises=[promise("p1", 365.0, "2027-01-04")])
    assert offers(r)[1].startswith("Friday, 1 January 2027 is not a business day.")


# ---------- other tools fail ----------

def test_a_failed_callback_booking_is_retried(spec, api, code):
    acts = [dict(stage="open", main="someone_else"),
            dict(stage="third", main="gives_time", day="tomorrow", time="eleven")]
    r = play(spec, api, code, "PUB-02", acts, faults={"book_callback": ["500"]})
    expect(r, "not_reached", "callback", callbacks=[{"date": "2026-10-15", "time": "11:00"}])


def test_a_failed_lookup_is_retried(spec, api, code):
    acts = verified("PUB-10") + [dict(stage="negotiate", main="accepts"), dict(stage="confirm", main="yes")]
    r = play(spec, api, code, "PUB-10", acts, faults={"lookup": ["timeout"]})
    expect(r, "promise", promises=[promise("p1", 365.0, "2026-10-14")])


@pytest.mark.parametrize("day,main", [("then", "proposes"), ("that day", "accepts"), ("on the day you said", "unclear")])
def test_a_day_that_points_back_accepts_the_offer(spec, api, code, day, main):
    acts = verified("PUB-07") + [
        dict(stage="negotiate", main="refuses"),
        dict(stage="negotiate", main="proposes", day="Monday the 26th"),
        dict(stage="negotiate", main=main, day=day, said="Yes, I can pay it " + day + "."),
        dict(stage="confirm", main="yes")]
    expect(play(spec, api, code, "PUB-07", acts), "promise", promises=[promise("p1", 540.0, "2026-10-27")])

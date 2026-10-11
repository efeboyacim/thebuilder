"""policy_lib: calendar helpers, numbers, spoken-number, date, time and answer normalizers, converters (item 08)."""
import datetime as dt

import pytest

from builder import bindings as bd
from builder import policy_lib as pl

D = dt.date
HOL = ["2026-10-26", "2026-12-25", "2027-01-01"]
CAL = pl.calendar(["mon", "tue", "wed", "thu", "fri"], HOL)
H = pl.helpers(CAL)


def test_business_days_skip_weekends_and_holidays():
    assert H["is_business_day"](D(2026, 10, 23)) and not H["is_business_day"](D(2026, 10, 24))
    assert not H["is_business_day"](D(2026, 10, 26))
    assert H["add_business_days"](D(2026, 10, 14), 7) == D(2026, 10, 23)
    assert H["add_business_days"](D(2026, 10, 14), 12) == D(2026, 11, 2)  # 26 Oct is a holiday
    assert H["add_business_days"](D(2026, 10, 23), 1) == D(2026, 10, 27)
    assert H["add_business_days"](D(2026, 10, 27), -1) == D(2026, 10, 23)
    assert H["add_business_days"](D(2026, 10, 24), 0) == D(2026, 10, 24)
    assert H["add_calendar_days"](D(2026, 12, 30), 3) == D(2027, 1, 2)
    assert H["weekday"](D(2026, 10, 16)) == 4


def test_roll_and_week_and_month_ends():
    assert H["roll"](D(2026, 10, 24), "next") == D(2026, 10, 27)
    assert H["roll"](D(2026, 10, 26), "prev") == D(2026, 10, 23)
    assert H["roll"](D(2026, 10, 22), "next") == D(2026, 10, 22)
    assert H["last_bd_of_week"](D(2026, 10, 14), 0) == D(2026, 10, 16)
    assert H["last_bd_of_week"](D(2026, 10, 16), 1) == D(2026, 10, 23)
    assert H["last_bd_of_week"](D(2026, 12, 21), 0) == D(2026, 12, 24)  # Christmas Friday
    assert H["last_bd_of_week"](D(2026, 12, 27), 0) == D(2026, 12, 24)  # Sunday belongs to its Monday's week
    assert H["last_bd_of_month"](D(2026, 10, 5)) == D(2026, 10, 30)
    assert H["last_bd_of_month"](D(2026, 12, 1)) == D(2026, 12, 31)
    assert H["last_bd_of_month"](D(2027, 1, 15)) == D(2027, 1, 29)


def test_six_day_week_and_unspecified_weekdays():
    six = pl.calendar(["mon", "tue", "wed", "thu", "fri", "sat"], [])
    assert pl.add_business_days(six, D(2026, 10, 16), 1) == D(2026, 10, 17)
    assert pl.calendar("not_specified", [])["wd"] == (0, 1, 2, 3, 4)


def test_helpers_reject_wrong_types():
    with pytest.raises(TypeError):
        H["add_business_days"](D(2026, 10, 14), 2.5)
    with pytest.raises(TypeError):
        H["add_business_days"]("2026-10-14", 2)
    with pytest.raises(ValueError):
        H["roll"](D(2026, 10, 24), "forward")


@pytest.mark.parametrize("x,p,want", [(2.675, 2, 2.68), (1.005, 2, 1.01), (637.5, 0, 638.0), (-2.5, 0, -3.0),
                                      (0.125, 2, 0.13), (12.3449, 2, 12.34), (1234.565, 2, 1234.57)])
def test_round_half_up(x, p, want):
    assert pl.round_half_up(x, p) == want


def test_ceil_to():
    assert pl.ceil_to(12.01, 5) == 15 and pl.ceil_to(15, 5) == 15 and pl.ceil_to(0.07, 0.05) == 0.1


@pytest.mark.parametrize("x", [0, 0.01, 0.1, 0.29, 1.005, 2.675, 99.995, 420, 637.5, 1234.565, 250.5, 12345.678])
def test_converters_equal_the_bindings_converters(x):
    for name in ("number_2dp", "cents_int", "string_2dp"):
        assert pl.CONVERT[name](x) == bd.CONVERTERS[name][0](x), name
    assert pl.date_iso(D(2026, 10, 14)) == bd.date_iso(D(2026, 10, 14)) == "2026-10-14"
    assert pl.time_hhmm("9:05") == bd.time_hhmm("9:05") == "09:05"
    assert pl.datetime_join("2026-10-15", "11:00") == bd.datetime_join("2026-10-15", "11:00")


@pytest.mark.parametrize("text,want", [
    ("fourteen", [14]), ("the fourteenth", [14]), ("twenty-first", [21]), ("nineteen eighty-eight", [1988]),
    ("two thousand and one", [2001]), ("nineteen oh five", [1905]), ("twenty twenty-six", [2026]),
    ("three hundred and sixty-five", [365]), ("one thousand two hundred fifty", [1250]),
    ("14 3 1988", [14, 3, 1988]), ("fourteen three nineteen eighty eight", [14, 3, 1988])])
def test_spoken_numbers(text, want):
    assert [n[0] for n in pl.spoken_numbers(text)] == want


@pytest.mark.parametrize("text,want", [("four four seven one", "4471"), ("forty-four seventy-one", "4471"),
                                       ("oh three five eight", "0358"), ("double five one two", "5512"),
                                       ("44 71", "4471"), ("It's 4471.", "4471")])
def test_spoken_digits(text, want):
    assert pl.spoken_digits(text) == want


@pytest.mark.parametrize("text,order,want", [
    ("14 March 1988", "day_month_year", "1988-03-14"), ("March 14, 1988", "day_month_year", "1988-03-14"),
    ("the fourteenth of March nineteen eighty-eight", "day_month_year", "1988-03-14"),
    ("14/03/1988", "day_month_year", "1988-03-14"), ("03/14/1988", "day_month_year", "1988-03-14"),
    ("03/04/1988", "month_day_year", "1988-03-04"), ("1988-03-14", "day_month_year", "1988-03-14"),
    ("14.3.88", "day_month_year", "1988-03-14"), ("21st of July 1990", "day_month_year", "1990-07-21"),
    ("fourteen three nineteen eighty eight", "day_month_year", "1988-03-14")])
def test_dates(text, order, want):
    assert pl.iso_date(pl.date_parts(text, order, D(2026, 10, 14))) == want


def test_answers_accumulate_across_turns():
    q = {"answer_type": "date", "numeric_date_order": "day_month_year", "complete_pattern": "[0-9]{4}-[0-9]{2}-[0-9]{2}"}
    v, done, part = pl.normalize_answer(q, "the fourteenth of March", None)
    assert not done and part == {"day": 14, "month": 3}
    v, done, part = pl.normalize_answer(q, "1988", part)
    assert done and v == "1988-03-14"
    d = {"answer_type": "digits", "digits_count": 4, "complete_pattern": "[0-9]{4}"}
    v, done, part = pl.normalize_answer(d, "forty four", None)
    assert (v, done, part) == ("44", False, "44")
    assert pl.normalize_answer(d, "seventy one", part)[:2] == ("4471", True)
    assert pl.normalize_answer(d, "4471, sorry, 4417", None)[:2] == ("4417", True)
    y = {"answer_type": "year", "complete_pattern": "[0-9]{4}"}
    assert pl.normalize_answer(y, "nineteen seventy-nine", None)[:2] == ("1979", True)
    pc = {"answer_type": "letters_digits", "complete_pattern": "[A-Z]{2}[0-9]{2}[A-Z]{2}"}
    assert pl.normalize_answer(pc, "a b one two c d", None)[:2] == ("AB12CD", True)
    assert pl.normalize_answer(pc, "AB1 2CD", None)[:2] == ("AB12CD", True)


@pytest.mark.parametrize("text,want", [
    ("around eleven", (11, 0, "")), ("11:30", (11, 30, "")), ("10.30", (10, 30, "")), ("2 pm", (14, 0, "pm")),
    ("half past two", (2, 30, "")), ("quarter to three", (2, 45, "")), ("eleven thirty", (11, 30, "")),
    ("eleven o'clock", (11, 0, "")), ("at three in the afternoon", (15, 0, "pm")), ("noon", (12, 0, "pm")),
    ("7 p.m.", (19, 0, "pm")), ("tomorrow", None), ("the 15th", None)])
def test_times(text, want):
    assert pl.parse_time(text) == want


def test_time_candidates():
    assert pl.time_candidates((2, 30, "")) == [(2, 30), (14, 30)]
    assert pl.time_candidates((14, 0, "pm")) == [(14, 0)]


@pytest.mark.parametrize("text,today,want", [
    ("tomorrow", D(2026, 10, 14), D(2026, 10, 15)), ("today", D(2026, 10, 14), D(2026, 10, 14)),
    ("the day after tomorrow", D(2026, 10, 14), D(2026, 10, 16)), ("Friday", D(2026, 10, 14), D(2026, 10, 16)),
    ("next Monday", D(2026, 10, 14), D(2026, 10, 19)), ("Monday", D(2026, 10, 16), D(2026, 10, 19)),
    ("6 November", D(2026, 10, 14), D(2026, 11, 6)), ("the 2nd", D(2026, 10, 14), D(2026, 11, 2)),
    ("15 January", D(2026, 12, 20), D(2027, 1, 15)), ("whenever", D(2026, 10, 14), None)])
def test_days(text, today, want):
    assert pl.resolve_day(text, today) == want


def test_calendar_week_mode():
    assert pl.resolve_day("next Friday", D(2026, 10, 16), "calendar_week") == D(2026, 10, 23)
    assert pl.resolve_day("this Friday", D(2026, 10, 14), "calendar_week") == D(2026, 10, 16)


def test_parse_now_and_spoken_formats():
    assert pl.parse_now("2026-10-14T11:00") == dt.datetime(2026, 10, 14, 11, 0)
    assert pl.parse_now("2026-10-14 11:00:30Z") == dt.datetime(2026, 10, 14, 11, 0)
    assert pl.parse_now("2026-10-14") == dt.datetime(2026, 10, 14) and pl.parse_now("soon") is None
    assert pl.say_money(420.0) == "420" and pl.say_money(310.5) == "310.50"
    assert pl.say_date(D(2026, 10, 16), D(2026, 10, 14)) == "Friday, 16 October"
    assert pl.say_date(D(2027, 1, 4), D(2026, 12, 30)) == "Monday, 4 January 2027"
    assert pl.render("Hello {{title}} {{last_name}} , {{missing}}.", {"title": "Ms", "last_name": "Lee"}) == "Hello Ms Lee,."


@pytest.mark.parametrize("text,want", [
    ("Nineteen October nineteen eighty three", "1983-10-19"), ("twenty May nineteen ninety", "1990-05-20"),
    ("October nineteenth, nineteen eighty-three", "1983-10-19"), ("the twentieth of January twenty twenty", "2020-01-20"),
    ("twenty-third February nineteen eighty nine", "1989-02-23"), ("19/10/1983", "1983-10-19")])
def test_day_words_never_merge_with_the_year_across_the_month(text, want):
    assert pl.iso_date(pl.date_parts(text)) == want

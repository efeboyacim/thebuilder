"""Brain library: calendar and expression helpers, spoken-number, date, time and answer normalizers, body converters,
template rendering and spoken formats.

This file runs inside the Freya code node (builder/brain.py inlines it into the brain) and locally. Rules:
- imports json, re and datetime only;
- no `global` or `nonlocal`, and no function rebinds a module-level name (the brain wraps this file in a function);
- never two opening or two closing braces in a row anywhere (the platform may template the code field), and no
  f-strings;
- no multi-line string data (the brain indents every line).
Nothing here knows a brief: every bank value comes from the spec at call time.
"""
import datetime, json, re

DAY = datetime.timedelta(days=1)
WEEKDAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_WORDS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTH_WORDS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
               "november", "december")
PLACEHOLDER = re.compile(r"\x7b\x7b\s*([A-Za-z_][A-Za-z0-9_]*)\s*\x7d\x7d")


# ---------- small coercions ----------

def truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes")


def empty(v):
    return v is None or str(v).strip().lower() in ("", "none", "null", "n/a", "-", "unknown")


def to_num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        x = float(str(v).strip().replace(",", ""))
    except ValueError:
        return None
    return int(x) if x == int(x) and "." not in str(v) else x


def to_int(v):
    x = to_num(v)
    return None if x is None else int(x)


def _need_date(d):
    if isinstance(d, datetime.datetime):
        return d.date()
    if not isinstance(d, datetime.date):
        raise TypeError("expected a date, got " + type(d).__name__)
    return d


def _need_int(n):
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("expected a whole number, got " + repr(n))
    return n


def _need_num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise TypeError("expected a number, got " + repr(x))
    return x


# ---------- clock ----------

def parse_now(s):
    """The call's `now` as a datetime (seconds and any zone suffix ignored), or None."""
    m = re.match(r"\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2}))?", str(s or ""))
    if not m:
        return None
    try:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0),
                                 int(m.group(5) or 0))
    except ValueError:
        return None


# ---------- calendar ----------

def calendar(business_weekdays, holidays):
    """A calendar from the spec. business_weekdays 'not_specified' -> Monday to Friday (docs/design.md 2.5)."""
    if isinstance(business_weekdays, list):
        wd = tuple(sorted(WEEKDAY_CODES.index(w) for w in business_weekdays if w in WEEKDAY_CODES))
    else:
        wd = (0, 1, 2, 3, 4)
    return dict(wd=wd, hol=frozenset(str(h) for h in holidays or []))


def is_business_day(cal, d):
    d = _need_date(d)
    return d.weekday() in cal["wd"] and d.isoformat() not in cal["hol"]


def add_business_days(cal, d, n):
    """The n-th business day after d (before d for n < 0); d itself for n == 0."""
    d, n = _need_date(d), _need_int(n)
    step, left, guard = (DAY if n >= 0 else -DAY), abs(n), 0
    while left > 0:
        d = d + step
        if is_business_day(cal, d):
            left -= 1
        guard += 1
        if guard > 4000:
            raise ValueError("no business days in the calendar")
    return d


def add_calendar_days(cal, d, n):
    return _need_date(d) + _need_int(n) * DAY


def weekday(cal, d):
    return _need_date(d).weekday()


def roll(cal, d, direction):
    """d when it is a business day, else the next ('next') or previous ('prev') business day."""
    d = _need_date(d)
    if direction not in ("next", "prev"):
        raise ValueError("roll direction must be 'next' or 'prev', got " + repr(direction))
    guard = 0
    while not is_business_day(cal, d):
        d = d + (DAY if direction == "next" else -DAY)
        guard += 1
        if guard > 4000:
            raise ValueError("no business days in the calendar")
    return d


def last_bd_of_week(cal, d, offset_weeks):
    """The last business day of d's week (Monday to Sunday), offset by whole weeks."""
    d = _need_date(d)
    monday = d - d.weekday() * DAY + 7 * _need_int(offset_weeks) * DAY
    x = monday + 6 * DAY
    while x >= monday:
        if is_business_day(cal, x):
            return x
        x = x - DAY
    return roll(cal, monday - DAY, "prev")


def last_bd_of_month(cal, d):
    d = _need_date(d)
    first_next = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return roll(cal, first_next - DAY, "prev")


def ceil_to(x, step):
    x, step = _need_num(x), _need_num(step)
    if step <= 0:
        raise ValueError("ceil_to step must be positive")
    q = x / step
    n = int(q)
    if n < q - 1e-9:
        n += 1
    return round(n * step, 10)


def round_half_up(x, places):
    """x rounded half away from zero to `places` decimals, read from its decimal form (no binary surprises)."""
    x, p = _need_num(x), _need_int(places)
    if p < 0 or p > 10:
        raise ValueError("round_half_up places must be 0 to 10")
    s = "%.12f" % abs(x)
    whole, frac = s.split(".")
    n = int(whole + frac[:p]) + (1 if int(frac[p]) >= 5 else 0)
    r = n / (10 ** p)
    return -r if x < 0 else r


def helpers(cal):
    """The expression helpers (spec.HELPERS), bound to one calendar."""
    return dict(is_business_day=lambda d: is_business_day(cal, d),
                add_business_days=lambda d, n: add_business_days(cal, d, n),
                add_calendar_days=lambda d, n: add_calendar_days(cal, d, n),
                weekday=lambda d: weekday(cal, d),
                roll=lambda d, direction: roll(cal, d, direction),
                last_bd_of_week=lambda d, offset_weeks: last_bd_of_week(cal, d, offset_weeks),
                last_bd_of_month=lambda d: last_bd_of_month(cal, d),
                min=min, max=max, abs=abs, ceil_to=ceil_to, round_half_up=round_half_up)


# ---------- spoken numbers ----------

_SMALL = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
          "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_ORD_SMALL = ("zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
              "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
              "eighteenth", "nineteenth")
_ORD_TENS = ("", "", "twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth", "seventieth", "eightieth",
             "ninetieth")


def _word_table():
    t = dict(oh=(0, "z"), o=(0, "z"), nought=(0, "z"), nil=(0, "z"), hundred=(100, "h"), thousand=(1000, "k"))
    for i, w in enumerate(_SMALL):
        t[w] = (i, "z" if i == 0 else ("u" if i < 10 else "t"))
    for i, w in enumerate(_TENS):
        if w:
            t[w] = (i * 10, "d")
    for i, w in enumerate(_ORD_SMALL):
        t[w] = (i, "ou" if i < 10 else "ot")
    for i, w in enumerate(_ORD_TENS):
        if w:
            t[w] = (i * 10, "od")
    t["fourty"] = t["forty"]
    return t


WORDS = _word_table()


def _tokens(text):
    return re.findall(r"\d+(?:st|nd|rd|th)?|[a-z]+", str(text or "").lower().replace("-", " "))


def spoken_numbers(text):
    """Numbers in a phrase, in order, as [value, is_ordinal, from_words]. Digits and English number words
    ('fourteenth', 'three hundred and five', 'two thousand and one'); 'nineteen eighty-eight' and 'twenty twenty'
    become one year, 'nineteen oh five' too."""
    out, g = [], []  # g: the open word group [total, chunk, last kind, ordinal]
    for t in _tokens(text) + ["."]:
        if t[0].isdigit():
            if g:
                out.append([g[0] + g[1], g[3], True])
                del g[:]
            m = re.match(r"(\d+)(st|nd|rd|th)?$", t)
            out.append([int(m.group(1)), bool(m.group(2)), False])
            continue
        w = WORDS.get(t)
        if w is None:
            if t == "and" and g and g[2] in ("h", "k"):
                continue
            if g:
                out.append([g[0] + g[1], g[3], True])
                del g[:]
            continue
        val, kind = w
        base = kind[-1]
        if g and not g[3]:
            last, chunk = g[2], g[1]
            if base == "u" and (last in ("h", "k") or (last == "d" and chunk % 10 == 0)):
                g[1], g[2], g[3] = chunk + val, base, kind[0] == "o"
                continue
            if base in ("t", "d") and last in ("h", "k"):
                g[1], g[2], g[3] = chunk + val, base, kind[0] == "o"
                continue
            if kind == "h" and last in ("u", "t", "d") and chunk < 100:
                g[1], g[2] = chunk * 100, "h"
                continue
            if kind == "k" and last in ("u", "t", "d", "h"):
                g[0], g[1], g[2] = g[0] + chunk * 1000, 0, "k"
                continue
        if g:
            out.append([g[0] + g[1], g[3], True])
            del g[:]
        if kind == "h":
            g.extend([0, 100, "h", False])
        elif kind == "k":
            g.extend([1000, 0, "k", False])
        else:
            g.extend([0, val, base, kind[0] == "o"])
    merged, i = [], 0
    while i < len(out):  # spoken years: 19|88, 20|20, 19|0|5
        a = out[i]
        if a[2] and not a[1] and a[0] in (19, 20) and i + 1 < len(out) and out[i + 1][2] and not out[i + 1][1]:
            b = out[i + 1]
            if 10 <= b[0] <= 99:
                merged.append([a[0] * 100 + b[0], False, True])
                i += 2
                continue
            if b[0] == 0 and i + 2 < len(out) and out[i + 2][2] and 1 <= out[i + 2][0] <= 9:
                merged.append([a[0] * 100 + out[i + 2][0], False, True])
                i += 3
                continue
        merged.append(a)
        i += 1
    return merged


def spoken_digits(text):
    """The digit string of a code said in digits or words: 'four four seven one', 'forty-four seventy-one',
    'oh three five eight', 'double five', '44 71'."""
    toks, out, rep, i = _tokens(text), "", 1, 0
    if any(t in ("hundred", "thousand") for t in toks):
        return "".join(str(n[0]) for n in spoken_numbers(text))
    while i < len(toks):
        t = toks[i]
        w = WORDS.get(t)
        if t[0].isdigit():
            d = re.match(r"\d+", t).group(0)
            out += d * rep if len(d) == 1 else d
            rep = 1
        elif t in ("double", "triple"):
            rep = 2 if t == "double" else 3
        elif w and w[1] in ("z", "u"):
            out += str(w[0]) * rep
            rep = 1
        elif w and w[1] == "t":
            out += str(w[0])
        elif w and w[1] == "d":
            nxt = WORDS.get(toks[i + 1]) if i + 1 < len(toks) else None
            if nxt and nxt[1] == "u":
                out += str(w[0] + nxt[0])
                i += 1
            else:
                out += str(w[0])
        i += 1
    return out


# ---------- dates ----------

_MONTH_RE = re.compile(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
                       r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b")


def _month_of(word):
    return next(i + 1 for i, m in enumerate(MONTH_WORDS) if m.startswith(word[:3]))


def _year4(y, today):
    if y >= 100:
        return y
    pivot = (today.year % 100) if today else 30
    return (1900 if y > pivot else 2000) + y


def _valid(y, m, d):
    try:
        datetime.date(y, m, d)
        return True
    except (ValueError, TypeError):
        return False


def date_parts(text, order="day_month_year", today=None):
    """The day, month and year a phrase names, as a dict with any of the keys day, month, year. A date said only in
    numbers is read in `order`; when that reading is impossible and the swapped one is not, the swapped one."""
    s = str(text or "").lower()
    m = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", s)
    if m:
        return dict(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{2,4}))?\b", s)
    nums = []
    month = None
    if m:
        nums = [int(m.group(1)), int(m.group(2))] + ([int(m.group(3))] if m.group(3) else [])
    else:
        mw = _MONTH_RE.search(s)
        if mw:  # numbers before and after the month word never merge ('nineteen October nineteen eighty')
            month = _month_of(mw.group(1))
            nums = [n[0] for n in spoken_numbers(s[:mw.start()])] + [n[0] for n in spoken_numbers(s[mw.end():])]
        else:
            nums = [n[0] for n in spoken_numbers(s)]
    out = dict()
    if month:
        out["month"] = month
        for n in nums:
            if "year" not in out and (n >= 1000 or (n > 31 and n < 100)):
                out["year"] = _year4(n, today)
            elif "day" not in out and 1 <= n <= 31:
                out["day"] = n
            elif "year" not in out and n < 100 and "day" in out:
                out["year"] = _year4(n, today)
        return out
    if not nums:
        return out
    year = None
    rest = []
    for n in nums:
        if year is None and n >= 1000:
            year = n
        else:
            rest.append(n)
    if order == "year_month_day" and year is None and len(rest) == 3:
        year, rest = _year4(rest[0], today), rest[1:]
    if year is None and len(rest) == 3:
        year, rest = _year4(rest[2], today), rest[:2]
    if year is not None:
        out["year"] = year
    if len(rest) >= 2:
        a, b = rest[0], rest[1]
        day, mon = (b, a) if order == "month_day_year" else (a, b)
        if mon > 12 and day <= 12:
            day, mon = mon, day
        out["day"], out["month"] = day, mon
    elif len(rest) == 1 and 1 <= rest[0] <= 31:
        out["day"] = rest[0]
    return out


def iso_date(parts):
    if all(k in parts for k in ("year", "month", "day")) and _valid(parts["year"], parts["month"], parts["day"]):
        return "%04d-%02d-%02d" % (parts["year"], parts["month"], parts["day"])
    return ""


def _weeks_ahead(s):
    """0 for 'this', 1 for 'next'/'following', 2 for 'the week after next'; None when none is said."""
    if re.search(r"\bweek after next\b|\bafter next\b", s):
        return 2
    if re.search(r"\bnext\b|\bfollowing\b", s):
        return 1
    if re.search(r"\bthis\b|\bthe current\b", s):
        return 0
    return None


def resolve_day(text, today, weekday_mode="nearest", order="day_month_year"):
    """The date a customer means by a day phrase, resolved against today: today, tomorrow, the day after tomorrow,
    'in three days', a weekday (with this/next), an explicit date, or a day of the month (a weekday said with a day
    number is read by the number). None when the phrase names no day: a week, a month or an event is vague.
    A weekday said alone is the first such day from today on. weekday_mode 'calendar_week': a weekday after 'this' is
    that day in the current Monday-to-Sunday week (it may have passed), after 'next' the one in the following week,
    'the week after next' the week after that; 'nearest': 'this' and 'next' both mean the first such day after
    today, 'the week after next' one week later."""
    s = " " + re.sub(r"[,.!?;]", " ", str(text or "").lower()) + " "
    if re.search(r"\bday after tomorrow\b", s):
        return today + 2 * DAY
    if re.search(r"\btomorrow\b", s):
        return today + DAY
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", s):
        return today
    if re.search(r"\b(few|couple|several|some)\b", s):
        return None
    m = re.search(r"\b(?:in|within|after)\s+(.+?)\s+days?\b|\b(\w+(?:\s\w+)?)\s+days?\s+(?:from (?:now|today)|time)\b", s)
    if m:
        nums = spoken_numbers(m.group(1) or m.group(2))
        if not nums and re.search(r"\ba\b|\bone\b", m.group(1) or m.group(2)):
            nums = [[1]]
        return today + nums[-1][0] * DAY if nums and nums[-1][0] <= 366 else None
    if _MONTH_RE.search(s) is None and re.search(r"\bweeks?\b|\bmonths?\b|\byears?\b|\bweekend\b", s) and \
            not any(re.search(r"\b" + w[:3] + r"(" + w[3:] + r")?\b", s) for w in WEEKDAY_WORDS):
        return None  # a period, not a day
    p = date_parts(text, order, today) if re.search(r"\d|" + "|".join(_ORD_SMALL[1:] + _ORD_TENS[2:]) + "|" +
                                                     _MONTH_RE.pattern[2:-2], s) else dict()
    if "day" not in p:
        for i, w in enumerate(WEEKDAY_WORDS):
            if re.search(r"\b" + w[:3] + r"(" + w[3:] + r")?\b", s):
                ahead = _weeks_ahead(s)
                if ahead is None:
                    return today + ((i - today.weekday()) % 7) * DAY
                if weekday_mode == "calendar_week":
                    return today - today.weekday() * DAY + (i + 7 * ahead) * DAY
                d = today + ((i - today.weekday() - 1) % 7 + 1) * DAY
                return d + 7 * DAY if ahead == 2 else d
        return None
    if "day" in p:
        y, m = p.get("year", today.year), p.get("month")
        if m is None:
            m = today.month
            if not _valid(y, m, p["day"]) or datetime.date(y, m, p["day"]) < today:
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        elif "year" not in p and _valid(y, m, p["day"]) and datetime.date(y, m, p["day"]) < today:
            y += 1
        if _valid(y, m, p["day"]):
            return datetime.date(y, m, p["day"])
    return None


# ---------- amounts ----------

def parse_amount(text, due=None, floor=None):
    """The amount a customer offers: digits ('82.40', '3,100', '90 pounds and 5 pence'), words ('nine hundred and
    twelve forty', 'four hundred point five'), or the minimum / the full amount. None when it names none."""
    s = " " + str(text or "").lower().replace("-", " ") + " "
    if re.search(r"\b(minimum|least)\b", s) and floor is not None:
        return float(floor)
    if re.search(r"\b(full|whole|everything|all of it|entire|the lot)\b", s) and due is not None:
        return float(due)
    s = re.sub(r"\b(\d{1,3}(?:\.\d{3})+),(\d{1,2})\b", lambda m: m.group(1).replace(".", "") + "." + m.group(2), s)
    s = re.sub(r"\b(\d{1,3}(?:\.\d{3})+)\b(?![.,]\d)", lambda m: m.group(1).replace(".", ""), s)  # 1.250 = 1250
    s = re.sub(r"\b(\d+),(\d{1,2})\b(?![\d,])", r"\1.\2", s)  # a decimal comma: 463,51
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", s)
    if nums:
        v = float(nums[0].replace(",", ""))
        if len(nums) > 1 and "." not in nums[0] and re.search(r"\b(pence|cents?|p)\b", s) and float(nums[1]) < 100:
            v += float(nums[1]) / 100
        return v
    words = spoken_numbers(s)
    if not words:
        return None
    v = float(words[0][0])
    if len(words) > 1 and words[1][0] < 100:
        if re.search(r"\bpoint\b", s):
            v += float("0." + str(words[1][0]))
        elif v >= 100 or re.search(r"\b(pounds?|euros?|dollars?|pence|cents?)\b", s):
            v += words[1][0] / 100.0
        elif 10 <= v < 100 and 10 <= words[1][0] and due is not None and \
                (floor or 0) <= v * 100 + words[1][0] <= float(due):
            v = v * 100 + words[1][0]  # 'twelve fifty' said for 1250
    return v


# ---------- times ----------

def parse_time(text):
    """(hour, minute, meridiem) from a time phrase; meridiem is 'am', 'pm' or '' when not said. None if no time."""
    s = " " + re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", str(text or "").lower()) + " "
    mer = "pm" if re.search(r"\b(pm|afternoon|evening|tonight)\b", s) else (
        "am" if re.search(r"\b(am|morning)\b", s) else "")
    if re.search(r"\b(noon|midday)\b", s):
        return (12, 0, "pm")
    if re.search(r"\bmidnight\b", s):
        return (0, 0, "am")
    m = re.search(r"\b(\d{1,2})[:h.](\d{2})\b", s)
    if m:
        return _apply(int(m.group(1)), int(m.group(2)), mer)
    m = re.search(r"\b(half|quarter|\d{1,2}|[a-z]+) (past|to|after|before) ([a-z]+|\d{1,2})\b", s)
    if m:
        hour = _hour_word(m.group(3))
        mins = 30 if m.group(1) == "half" else (15 if m.group(1) == "quarter" else _hour_word(m.group(1)))
        if hour is not None and mins is not None and mins < 60:
            if m.group(2) in ("to", "before"):
                return _apply((hour - 1) % 24, 60 - mins, mer)
            return _apply(hour, mins, mer)
    toks = _tokens(s)
    for i, t in enumerate(toks):
        h = _hour_word(t)
        if h is None or h > 24:
            continue
        mins = 0
        if i + 1 < len(toks):
            nums = spoken_numbers(" ".join(toks[i + 1:i + 3]))
            if nums and nums[0][0] < 60 and toks[i + 1] not in ("o", "oclock", "clock") and \
                    (WORDS.get(toks[i + 1]) or toks[i + 1].isdigit()):
                mins = nums[0][0]
        return _apply(h, mins, mer)
    return None


def _hour_word(t):
    if t.isdigit():
        return int(t)
    w = WORDS.get(t)
    if w and w[1] in ("u", "t", "z") and w[0] <= 23:
        return w[0]
    return None


def _apply(h, m, mer):
    if h > 23 or m > 59:
        return None
    if mer == "pm" and h < 12:
        h += 12
    if mer == "am" and h == 12:
        h = 0
    return (h, m, mer)


def time_candidates(t):
    """Readings of a parsed time: the stated one, and for an hour said without am/pm also the other half of the day."""
    if t is None:
        return []
    h, m, mer = t
    out = [(h, m)]
    if not mer and 1 <= h <= 11:
        out.append((h + 12, m))
    return out


def hhmm(h, m):
    return "%02d:%02d" % (h, m)


# ---------- answers (identity) ----------

def normalize_answer(q, text, partial=None, today=None):
    """(value, complete, partial) for a security answer. q: the spec question; partial: what earlier turns gave
    (a dict for dates, a string otherwise). value is the tool form when complete."""
    kind = q.get("answer_type")
    order = q.get("numeric_date_order")
    order = order if order in ("day_month_year", "month_day_year", "year_month_day") else "day_month_year"
    pattern = q.get("complete_pattern") or ""
    if kind == "date":
        parts = dict(partial) if isinstance(partial, dict) else dict()
        new = date_parts(text, order, today)
        if all(k in new for k in ("day", "month", "year")):
            parts = new
        else:
            parts.update(new)
        value = iso_date(parts)
        if value and (not pattern or re.fullmatch(pattern, value)):
            return value, True, dict()
        return value, False, parts
    if kind == "digits":
        n = to_int(q.get("digits_count"))
        new = spoken_digits(text)
        prev = partial if isinstance(partial, str) else ""
        if n and len(new) >= n:
            value = new[-n:]
        else:
            value = prev + new
            if n and len(value) > n:
                value = value[-n:]
    elif kind == "year":
        years = [x[0] for x in spoken_numbers(text) if x[0] >= 1000 or 30 <= x[0] < 100]
        value = str(_year4(years[-1], today)) if years else ""
    elif kind == "letters_digits":
        toks = re.findall(r"[a-z]+|\d+|[^\sa-z\d]", str(text or "").lower())
        value = "".join(str(WORDS[t][0]) if t in WORDS and WORDS[t][1] in ("z", "u") and len(t) > 1 else t
                        for t in toks)
        value = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    else:
        value = re.sub(r"\s+", " ", str(text or "")).strip()
    complete = bool(value) and (re.fullmatch(pattern, value) is not None if pattern else True)
    return value, complete, ("" if complete else value)


# ---------- body converters (equal to builder.bindings.CONVERTERS) ----------

def number_2dp(x):
    return round_half_up(float(x), 2)


def cents_int(x):
    return int(round_half_up(float(x) * 100, 0))


def string_2dp(x):
    return "%.2f" % round_half_up(float(x), 2)


def date_iso(x):
    if isinstance(x, (datetime.date, datetime.datetime)):
        return x.strftime("%Y-%m-%d")
    return datetime.date.fromisoformat(str(x)[:10]).isoformat()


def time_hhmm(x):
    if isinstance(x, (datetime.time, datetime.datetime)):
        return x.strftime("%H:%M")
    m = re.fullmatch(r"\s*(\d{1,2})[:.h](\d{2})(?::\d{2})?\s*", str(x))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("not a time: " + repr(x))
    return "%02d:%s" % (int(m.group(1)), m.group(2))


def datetime_join(d, t):
    return date_iso(d) + "T" + time_hhmm(t) + ":00"


CONVERT = dict(number_2dp=number_2dp, cents_int=cents_int, string_2dp=string_2dp, date_iso=date_iso,
               time_hhmm=time_hhmm, datetime_join=datetime_join)


# ---------- templates and spoken formats ----------

def render(template, values):
    """A template with every {{name}} filled; an unknown name becomes empty, never a literal placeholder."""
    def one(m):
        v = values.get(m.group(1))
        return "" if v is None else str(v)
    return re.sub(r"\s+([.,?!])", r"\1", re.sub(r"  +", " ", PLACEHOLDER.sub(one, str(template or "")))).strip()


def say_money(x):
    v = round_half_up(float(x), 2)
    return str(int(v)) if v == int(v) else "%.2f" % v


def say_date(d, today=None):
    d = _need_date(d)
    s = WEEKDAY_WORDS[d.weekday()].capitalize() + ", " + str(d.day) + " " + MONTH_WORDS[d.month - 1].capitalize()
    return s if today is not None and today.year == d.year else s + " " + str(d.year)


def say_time(h, m):
    return hhmm(h, m)


def join(*parts):
    return " ".join(str(p).strip() for p in parts if p is not None and str(p).strip())

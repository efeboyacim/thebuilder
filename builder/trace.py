"""Run trace: one JSON event per line in traces/<slot>-<UTC yyyymmdd-hhmmss>.jsonl, echoed to stdout.

Events are buffered until the trace file is opened (the slot is known only after ingest), so nothing is lost.
Every event is redacted before it is written: the values of the secret environment variables and the key
patterns below never reach the file or stdout.
"""
import datetime, json, os, re, sys, threading, time

SECRET_ENV = ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
              "CLAUDE_CODE_OAUTH_TOKEN")
SECRET_PATTERNS = (re.compile(r"fak_[A-Za-z0-9]+"), re.compile(r"sk-ant-[A-Za-z0-9_-]+"))
REDACTED = "[REDACTED]"
MIN_SECRET_LEN = 6  # shorter values would redact ordinary words


def utc_iso(ts=None):
    ts = time.time() if ts is None else ts
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")


def secret_values(environ=None):
    environ = os.environ if environ is None else environ
    return {v for v in (environ.get(k, "") for k in SECRET_ENV) if len(v) >= MIN_SECRET_LEN}


def redact(obj, secrets=()):
    """Return a copy of obj with secrets and key patterns replaced in every string, keys included."""
    if isinstance(obj, str):
        for s in sorted(secrets, key=len, reverse=True):
            obj = obj.replace(s, REDACTED)
        for p in SECRET_PATTERNS:
            obj = p.sub(REDACTED, obj)
        return obj
    if isinstance(obj, dict):
        return {redact(str(k), secrets): redact(v, secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [redact(v, secrets) for v in obj]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return redact(str(obj), secrets)


class Trace:
    def __init__(self, echo=True, stream=None):
        self.start = time.time()
        self.path = None
        self.echo = echo
        self.stream = stream
        self.events = []  # every event written, for the summary
        self._secrets = secret_values()  # captured now: a later stage may clear the environment
        self._buf = []
        self._file = None
        self._lock = threading.Lock()

    def open(self, trace_dir, slot):
        """Create the trace file and write the buffered events to it. Returns its path."""
        os.makedirs(trace_dir, exist_ok=True)
        path = os.path.join(trace_dir, f"{slot}-{utc_stamp(self.start)}.jsonl")
        with self._lock:
            self.path = path
            self._file = open(path, "w", encoding="utf-8", newline="\n")
            for line in self._buf:
                self._file.write(line + "\n")
            self._file.flush()
            self._buf = []
        return path

    def event(self, ev, t=None, **fields):
        rec = redact({"t": utc_iso(t), "ev": ev, **fields}, self._secrets | secret_values())
        line = json.dumps(rec, ensure_ascii=False, default=str)
        line = redact(line, self._secrets | secret_values())  # belt and braces for escaped values
        with self._lock:
            self.events.append(rec)
            if self._file:
                self._file.write(line + "\n")
                self._file.flush()
            else:
                self._buf.append(line)
            if self.echo:
                print(line, file=self.stream or sys.stdout, flush=True)
        return rec

    def warning(self, text, **fields):
        return self.event("warning", text=text, **fields)

    def close(self, status, **fields):
        """Write run_end and close the file. Status: ok, stopped (by --until) or error."""
        rec = self.event("run_end", seconds=round(time.time() - self.start, 1), status=status, **fields)
        with self._lock:
            if self._file:
                self._file.close()
                self._file = None
        return rec

    def count(self, ev):
        return sum(1 for e in self.events if e["ev"] == ev)

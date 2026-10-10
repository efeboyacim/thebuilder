"""Stage 1, ingest: read the input files and classify them by content, never by name or position.

- JSON object with an `openapi` key: the tool API (exactly one).
- JSON list whose items have `expected`: practice cases (at most one).
- Everything else: brief material, kept in the order given. Later briefs may amend earlier ones.

Only file paths are accepted. A directory is an error: each brief is one file.
"""
import dataclasses, hashlib, json, os, re

ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")  # latin-1 decodes any byte string
SLOT_PREFIX = "hub-"
SLOT_MAX = 64


class InputError(Exception):
    pass


@dataclasses.dataclass
class Input:
    path: str  # as given on the command line
    abspath: str
    kind: str  # brief | tool_api | cases
    text: str
    bytes: int
    sha256: str
    encoding: str
    data: object = None  # parsed JSON for tool_api and cases
    sections: list = dataclasses.field(default_factory=list)  # [(level, title)] for briefs

    def record(self):
        """The fields that go into the trace."""
        return {"path": self.path, "kind": self.kind, "bytes": self.bytes, "sha256": self.sha256,
                "encoding": self.encoding}


@dataclasses.dataclass
class Inputs:
    briefs: list
    tool_api: Input
    cases: object = None  # Input or None
    warnings: list = dataclasses.field(default_factory=list)

    @property
    def all(self):
        return self.briefs + [self.tool_api] + ([self.cases] if self.cases else [])


def read_text(path):
    """Return (text, encoding, raw bytes). utf-8 first, then single-byte fallbacks."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), ("utf-8" if enc == "utf-8-sig" else enc), raw
        except UnicodeDecodeError:
            continue
    raise InputError(f"{path}: cannot decode")  # unreachable: latin-1 accepts every byte


def classify(text, path=""):
    """Return (kind, parsed JSON or None)."""
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as e:
            if path.lower().endswith(".json"):
                raise InputError(f"{path}: not valid JSON ({e})") from None
            return "brief", None
        if isinstance(data, dict) and "openapi" in data:
            return "tool_api", data
        if isinstance(data, list) and data and all(isinstance(c, dict) and "expected" in c for c in data):
            return "cases", data
        return "brief", None
    if path.lower().endswith(".json"):
        raise InputError(f"{path}: has a .json name but is not JSON")
    return "brief", None


_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def sections(text):
    """Markdown ATX headings outside code fences, as [(level, title)]."""
    out, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        if m:
            out.append((len(m.group(1)), m.group(2)))
    return out


def ingest(paths):
    """Read and classify every path. Raises InputError on unusable inputs."""
    if not paths:
        raise InputError("no input files given")
    seen, items, warnings = set(), [], []
    for p in paths:
        if os.path.isdir(p):
            raise InputError(f"{p}: is a directory; give the brief and tool API files themselves")
        if not os.path.isfile(p):
            raise InputError(f"{p}: no such file")
        ap = os.path.normcase(os.path.realpath(p))
        if ap in seen:
            warnings.append(f"{p}: given twice, read once")
            continue
        seen.add(ap)
        text, enc, raw = read_text(p)
        if enc != "utf-8":
            warnings.append(f"{p}: not valid utf-8, read as {enc}")
        kind, data = classify(text, p)
        item = Input(path=p, abspath=os.path.realpath(p), kind=kind, text=text, bytes=len(raw),
                     sha256=hashlib.sha256(raw).hexdigest(), encoding=enc, data=data)
        if kind == "brief":
            if not text.strip():
                raise InputError(f"{p}: brief file is empty")
            item.sections = sections(text)
        items.append(item)

    apis = [i for i in items if i.kind == "tool_api"]
    cases = [i for i in items if i.kind == "cases"]
    briefs = [i for i in items if i.kind == "brief"]
    if not apis:
        raise InputError("no tool API file (a JSON object with an 'openapi' key) among the inputs")
    if len(apis) > 1:
        raise InputError("more than one tool API file: " + ", ".join(i.path for i in apis))
    if len(cases) > 1:
        raise InputError("more than one practice-case file: " + ", ".join(i.path for i in cases))
    if not briefs:
        raise InputError("no brief file among the inputs")
    return Inputs(briefs=briefs, tool_api=apis[0], cases=cases[0] if cases else None, warnings=warnings)


def sanitize_slot(slot):
    s = re.sub(r"[^A-Za-z0-9._+-]+|\.{2,}", "-", slot)
    s = re.sub(r"-{2,}", "-", s).strip(".-")[:SLOT_MAX]
    if not s:
        raise InputError(f"unusable slot name: {slot!r}")
    return s


def default_slot(briefs):
    """'hub-' + the brief stems joined by '+'. The prefix keeps our slots apart from the kit builder's."""
    stems = [os.path.splitext(os.path.basename(b.path))[0] for b in briefs]
    return sanitize_slot(SLOT_PREFIX + "+".join(stems))


def resolve_slot(cli_slot, environ, briefs):
    """--slot, else env BUILDER_SLOT, else the default from the brief names."""
    if cli_slot:
        return sanitize_slot(cli_slot)
    if environ.get("BUILDER_SLOT"):
        return sanitize_slot(environ["BUILDER_SLOT"])
    return default_slot(briefs)


def tool_api_summary(api):
    """A one-line description for the run summary. The full parse is the ToolAPI stage's job."""
    d = api.data
    ops = [(m, p) for p, item in (d.get("paths") or {}).items() if isinstance(item, dict)
           for m in item if m.lower() in ("get", "post", "put", "patch", "delete")]
    servers = [s.get("url") for s in d.get("servers") or [] if isinstance(s, dict) and s.get("url")]
    return {"openapi": d.get("openapi"), "operations": len(ops), "servers": servers}

#!/usr/bin/env python3
"""Leak lint: no literal from the public briefs, their tool API or the practice cases may appear in `builder/`.

    python3 tools/leak_lint.py [--root DIR] [--verbose]

The candidate literals are extracted at lint time, so this file holds no list of them. Sources:

- every `briefs/*.md`: dates, clock times, numbers with three or more digits or a decimal point, backticked spans
  that look like code (an underscore or a digit), double-quoted spans of two or more words, and two-word proper
  names that occur at least twice (a bank's name);
- `tools/openapi.json`: the server host, operation ids, property names and string enum values that look like code;
- `public-test-set.json`: string values that look like code, dates and numbers, and the customers' names.

Plain words (`ok`, `hint`, `paid`) are not candidates: they are too common to tell a leak from English.
Scanned: every text file below `builder/`. Tests live in the top-level `tests/` and are not scanned.
Exit 0 when clean, 1 with one line per finding.
"""
import argparse, json, pathlib, re, sys
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCAN_SUFFIXES = {".py", ".json", ".md", ".txt", ".jinja", ".tmpl", ".yaml", ".yml"}

DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
NUMBER = re.compile(r"(?<![\w.-])(\d+\.\d+|\d{3,})(?!\w|\.\d)")  # a sentence may end right after it
CODEY = re.compile(r"^(?=.*[_\d])[A-Za-z_][A-Za-z0-9_.-]*$")  # an identifier with an underscore or a digit
BACKTICK = re.compile(r"`([^`\n]+)`")
QUOTED = re.compile(r"[\"“]([^\"”\n]{3,200})[\"”]")
PROPER = re.compile(r"\b([A-Z][a-z]+ [A-Z][a-z]+)\b")


def _words(s):
    return len(re.findall(r"[A-Za-z]+", s))


def from_brief(text, src, out):
    for m in DATE.finditer(text):
        out.add(("date", m.group(0), src))
    for m in TIME.finditer(text):
        out.add(("time", m.group(0), src))
    for m in NUMBER.finditer(text):
        out.add(("number", m.group(1), src))
    for m in BACKTICK.finditer(text):
        span = m.group(1).strip()
        for tok in re.split(r"[\s:,/()]+", span):
            tok = tok.strip("\"'.")
            if tok and CODEY.match(tok) and not tok.replace(".", "").isdigit():
                out.add(("identifier", tok, src))
    for m in QUOTED.finditer(text):
        s = m.group(1).strip()
        if _words(s) >= 2:
            out.add(("sentence", s, src))
    names = {}
    for m in PROPER.finditer(text):
        names[m.group(1)] = names.get(m.group(1), 0) + 1
    for name, n in names.items():
        if n >= 2:
            out.add(("name", name, src))


def from_openapi(data, src, out):
    for s in data.get("servers") or []:
        host = urlparse(str(s.get("url", ""))).hostname
        if host:
            out.add(("host", host, src))

    def walk(node, key=None):
        if isinstance(node, dict):
            if isinstance(node.get("operationId"), str):
                out.add(("identifier", node["operationId"], src))
            for name in (node.get("properties") or {}) if isinstance(node.get("properties"), dict) else ():
                if CODEY.match(name):
                    out.add(("identifier", name, src))
            for v in node.get("enum") or [] if isinstance(node.get("enum"), list) else ():
                if isinstance(v, str) and CODEY.match(v):
                    out.add(("identifier", v, src))
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk(data)


def from_cases(data, src, out):
    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            if key in ("first_name", "last_name") and len(node) >= 3:
                out.add(("name", node, src))
            elif CODEY.match(node) and len(node) >= 4:
                out.add(("identifier", node, src))
            from_brief_values(node, src, out)
        elif isinstance(node, float) and not node.is_integer():
            out.add(("number", f"{node:.2f}", src))

    walk(data)


def from_brief_values(text, src, out):
    for m in DATE.finditer(text):
        out.add(("date", m.group(0), src))


def candidates(root):
    out = set()
    for p in sorted((root / "briefs").glob("*.md")):
        from_brief(p.read_text(encoding="utf-8"), p.relative_to(root).as_posix(), out)
    api = root / "tools" / "openapi.json"
    if api.exists():
        from_openapi(json.loads(api.read_text(encoding="utf-8")), api.relative_to(root).as_posix(), out)
    cases = root / "public-test-set.json"
    if cases.exists():
        from_cases(json.loads(cases.read_text(encoding="utf-8")), cases.relative_to(root).as_posix(), out)
    return out


def pattern(kind, lit):
    if kind in ("sentence", "name"):  # a sentence in any case; a name only capitalised ("grace" is a word)
        words = [re.escape(w) for w in re.findall(r"\S+", lit)]
        return re.compile(r"(?<![A-Za-z])" + r"\s+".join(words) + r"(?![A-Za-z])", re.I if kind == "sentence" else 0)
    if kind == "number" and "." in lit:  # 637.50 also as 637.5
        whole, frac = lit.split(".")
        return re.compile(r"(?<![\w.])" + re.escape(whole) + r"\." + re.escape(frac.rstrip("0") or "0") + r"0*(?![\w.])")
    return re.compile(r"(?<![\w-])" + re.escape(lit) + r"(?![\w-])")


def scan(root, cands):
    findings = []
    compiled = [(k, lit, src, pattern(k, lit)) for k, lit, src in sorted(cands)]
    for p in sorted((root / "builder").rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SCAN_SUFFIXES or "__pycache__" in p.parts:
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for kind, lit, src, rx in compiled:
                if rx.search(line):
                    findings.append((p.relative_to(root).as_posix(), n, kind, lit, src, line.strip()))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=str(ROOT), help="repository root (default: this checkout)")
    ap.add_argument("--verbose", action="store_true", help="also list the candidate literals")
    args = ap.parse_args(argv)
    root = pathlib.Path(args.root).resolve()
    cands = candidates(root)
    if args.verbose:
        for kind, lit, src in sorted(cands):
            print(f"candidate {kind:10} {lit!r}  ({src})")
    findings = scan(root, cands)
    for path, n, kind, lit, src, line in findings:
        print(f"{path}:{n}: {kind} {lit!r} from {src}: {line[:120]}")
    kinds = {}
    for k, _, _ in cands:
        kinds[k] = kinds.get(k, 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
    print(f"leak lint: {len(cands)} candidate literals ({summary}); "
          f"{'clean' if not findings else str(len(findings)) + ' finding(s)'}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())

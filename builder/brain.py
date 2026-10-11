"""The brain's code string: policy_lib + engine + the compact spec + the compiled expressions, in one function.

    code = source(spec, api)          # what the compiler puts into the brain code node
    run_code(code, vars)              # one brain run, locally (replay and tests), worst-case namespaces
    lint(code)                        # errors: doubled braces, size

Wrapping everything in `def _brain(vars)` makes the code independent of how the sandbox runs it: with separate
global and local namespaces, top-level functions could not see each other, nested ones always can. The spec is
embedded as a JSON string with its braces written as escapes, so no doubled brace ever appears in the code.
"""
import ast, builtins, copy, io, json, pathlib, re, tokenize

from builder import engine, exprs, policy_lib
from builder import spec as sp

_DROP = ("refs", "when", "trigger", "phrases", "residual_rules", "rule_index", "forbidden_codes", "required_content",
         "forbidden_before_verification", "does_not_count", "spec_version", "outcome_table", "api_format",
         "one_at_a_time", "may_answer_identity", "needs_clear_yes", "on_change", "recording_template",
         "stated_in_brief", "meaning")
MAX_BYTES = 150_000  # a hard stop only: 62-75 KB brains ran live (items 11-13); the trace records brain_bytes


class BrainError(Exception):
    pass


def _strip(node):
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in _DROP}
    if isinstance(node, list):
        return [_strip(v) for v in node]
    return node


def compact_spec(spec, api):
    """What the engine reads: the spec without sources, prose and indexes, plus the product row layout."""
    S = _strip(copy.deepcopy(spec))
    for path in exprs.expressions(spec):  # compiled into EXPRS; the engine never reads the text
        segs = sp.split_path(path)
        found, parent = sp.resolve(S, ".".join(str(x) if isinstance(x, str) else "[" + str(x) + "]" for x in segs[:-1]).replace(".[", "["))
        if found and isinstance(parent, dict) and segs[-1] in parent:
            parent[segs[-1]] = 1
    S["call_variables"] = [v["name"] for v in spec.get("call_variables") or [] if isinstance(v, dict)]
    products, fields = exprs.item_schema(spec, api)
    S["_api"] = {"products": products, "item_fields": fields}
    return S


def module_source(module):
    """A module's code without docstrings, comment lines, blank lines and `# brain: strip` lines, indented one space
    per level (a continuation line keeps a non-zero indent inside its brackets)."""
    text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    drop = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and \
                    isinstance(first.value.value, str) and len(node.body) > 1:
                drop.update(range(first.lineno, first.end_lineno + 1))
    lines = [line for n, line in enumerate(text.splitlines(), 1) if n not in drop and "# brain: strip" not in line]
    return minify("\n".join(lines) + "\n")


_WORD = (tokenize.NAME, tokenize.NUMBER)


def _space(a, b):
    if a.type in _WORD and b.type in _WORD + (tokenize.STRING,):
        return True
    if a.type == tokenize.STRING and b.type in _WORD + (tokenize.STRING,):
        return True
    return a.string in ("{", "}") and b.string in ("{", "}")  # never a doubled brace


def minify(text):
    """The same program with one space per indent level, one physical line per logical line, no comments and no
    spaces the tokenizer does not need."""
    out, line, level, prev = [], [], 0, None
    for t in tokenize.generate_tokens(io.StringIO(text).readline):
        if t.type in (tokenize.COMMENT, tokenize.NL, tokenize.ENCODING):
            continue
        if t.type == tokenize.INDENT:
            level += 1
            continue
        if t.type == tokenize.DEDENT:
            level -= 1
            continue
        if t.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
            if line:
                out.append(" " * level + "".join(line))
            line, prev = [], None
            continue
        if prev is not None and _space(prev, t):
            line.append(" ")
        line.append(t.string)
        prev = t
    return "\n".join(out) + "\n"


def _literal(text):
    """A Python string literal of text (ASCII only), with every brace written as an escape."""
    return ascii(text).replace("{", "\\x7b").replace("}", "\\x7d")


def source(spec, api):
    S = compact_spec(spec, api)
    srcs = exprs.sources(spec)
    tail = ["SPEC = json.loads(" + _literal(json.dumps(S, ensure_ascii=False, separators=(",", ":"))) + ")",
            "EXPRS = dict([" + "".join("(" + _literal(p) + ", lambda n, h: " + s + "), " for p, s in srcs.items()) + "])",
            "run(vars, SPEC, EXPRS)"]
    body = module_source(policy_lib) + module_source(engine) + "\n".join(tail) + "\n"
    code = "def _brain(vars):\n" + "".join(" " + l + "\n" for l in body.splitlines()) + "_brain(vars)\n"
    errs = lint(code)
    if errs:
        raise BrainError("; ".join(errs))
    return code


def lint(code):
    errs = []
    for pair in ("{{", "}}"):
        if pair in code:
            i = code.index(pair)
            errs.append(f"the brain code contains {pair!r} near {code[max(0, i - 40):i + 40]!r}")
    if len(code.encode("utf-8")) > MAX_BYTES:
        errs.append(f"the brain code has {len(code.encode('utf-8'))} bytes (limit {MAX_BYTES})")
    try:
        compile(code, "<brain>", "exec")
    except SyntaxError as e:
        errs.append(f"the brain code does not compile: {e}")
    return errs


def run_code(code, vars):
    """One brain run on `vars` (changed in place), with separate global and local namespaces."""
    exec(compile(code, "<brain>", "exec"), {"__builtins__": builtins}, {"vars": vars})
    return vars

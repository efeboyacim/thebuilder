"""Hardening (item 25): which spec errors still let the build go on."""
from builder import specgen


def test_soft_errors_keep_the_spec():
    assert specgen.soft("rule_index: digest rule R7 is not indexed (add its spec paths or a residual rule)")
    assert specgen.soft("phrases[2].text: 'x' is not verbatim in the brief")
    assert specgen.soft("opening.template: says 'recorded' before any write succeeded; ...")


def test_hard_errors_stop_the_build():
    assert not specgen.soft("situations[3].reason: 'x' is not one of the tool's values ['a']")
    assert not specgen.soft("products.bands[0].deadline: raises TypeError: ...")
    assert not specgen.soft("bindings.tools.lookup: the spec needs this tool role; bind it to a tool")

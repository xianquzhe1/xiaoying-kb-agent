"""工具层：检索、读取、引用校验、故障注入。"""

import json

import pytest

from kb_agent import Corpus, FaultInjection, ToolBox
from kb_agent.tools import ToolError


@pytest.fixture(scope="module")
def full():
    return Corpus.from_manifest("corpus_full.json")


def test_search_returns_both_conflicting_docs(full):
    hits = full.search("报销期限")
    top2 = {(h["doc_id"], h["section_id"]) for h in hits[:2]}
    assert top2 == {("D01", "S2"), ("D02", "S1")}


def test_variant_corpus_drops_d02():
    corpus = Corpus.from_manifest("corpus_no_d02.json")
    assert "D02" not in {s.doc_id for s in corpus.sections}
    assert corpus.search("报销期限")[0]["doc_id"] == "D01"


def test_zero_hit_lists_inventory(full):
    obs = json.loads(ToolBox(full).run("search_docs", {"query": "股票代码"}))
    assert obs["hit_count"] == 0
    assert "D05" in obs["corpus_inventory"]


def test_search_does_not_judge_conflict(full):
    # 工具只返回事实，不应出现"冲突"之类的结论字段
    obs = ToolBox(full).run("search_docs", {"query": "报销期限"})
    assert "冲突" not in obs and "conflict" not in obs


def test_read_unknown_section_raises_with_hint(full):
    with pytest.raises(ToolError, match="D01/S2"):
        ToolBox(full).run("read_doc", {"doc_id": "D01", "section_id": "S9"})


def test_contains_quote_is_verbatim(full):
    assert full.contains_quote("D01", "S2", "费用发生之日起30个自然日内")
    assert not full.contains_quote("D01", "S2", "费用发生之日起15个自然日内")
    assert not full.contains_quote("D01", "S9", "任意")


def test_fault_injection_fails_n_times_then_recovers(full):
    box = ToolBox(full, FaultInjection("search_docs", 1))
    with pytest.raises(ToolError):
        box.run("search_docs", {"query": "年休假"})
    assert json.loads(box.run("search_docs", {"query": "年休假"}))["hit_count"] >= 1


def test_fault_injection_multiple_tools(full):
    box = ToolBox(full, FaultInjection(("search_docs", "read_doc"), -1))
    with pytest.raises(ToolError):
        box.run("search_docs", {"query": "年休假"})
    with pytest.raises(ToolError):
        box.run("read_doc", {"doc_id": "D04", "section_id": "S1"})


def test_fault_injection_persistent(full):
    box = ToolBox(full, FaultInjection("search_docs", -1))
    for _ in range(3):
        with pytest.raises(ToolError):
            box.run("search_docs", {"query": "年休假"})
    # 其他工具不受影响
    box.run("read_doc", {"doc_id": "D04", "section_id": "S1"})

"""循环护栏：用 ScriptedModel 精确控制"模型"的每一步，验证代码层的约束，与模型能力无关。"""

from kb_agent import Corpus, FaultInjection, KnowledgeAgent, Limits, ToolBox
from kb_agent.models import ScriptedModel


def _agent(script, corpus="corpus_full.json", limits=None, faults=None):
    model = ScriptedModel(script)
    return KnowledgeAgent(model, ToolBox(Corpus.from_manifest(corpus), faults), limits), model


def _search(q):
    return {"name": "search_docs", "input": {"query": q, "rationale": "t"}}


def _read(d, s):
    return {"name": "read_doc", "input": {"doc_id": d, "section_id": s, "rationale": "t"}}


def _finish(status, citations=(), answer="a"):
    return {"name": "finish", "input": {"status": status, "answer": answer, "citations": list(citations), "rationale": "t"}}


GOOD = {"doc_id": "D04", "section_id": "S1", "quote": "每年享有带薪年休假5天"}


def test_happy_path_records_trace():
    agent, _ = _agent([[_search("年休假")], [_read("D04", "S1")], [_finish("answered", [GOOD])]])
    r = agent.ask("年假几天")
    assert r.status == "answered"
    assert [s.action for s in r.trace] == ["search_docs", "read_doc", "finish"]
    assert r.steps_used == 3 and r.tool_calls_used == 2
    assert all(s.rationale for s in r.trace)


def test_fabricated_quote_rejected_then_corrected():
    fake = {"doc_id": "D04", "section_id": "S1", "quote": "每年享有带薪年休假7天"}
    agent, model = _agent([[_read("D04", "S1")], [_finish("answered", [fake])], [_finish("answered", [GOOD])]])
    r = agent.ask("年假几天")
    assert r.status == "answered"
    rejected = r.trace[1]
    assert rejected.is_error and "未在对应原文中逐字找到" in rejected.observation
    # 拒收原因作为 is_error 的 tool_result 回灌给模型
    fed_back = model.seen_messages[2][-1]["content"][0]
    assert fed_back["is_error"] and "finish 未通过校验" in fed_back["content"]


def test_citing_unread_section_rejected():
    # 引用内容逐字正确，但该片段从未 read_doc 过：仍然拒收
    agent, _ = _agent([[_search("年休假")], [_finish("answered", [GOOD])], [_read("D04", "S1")], [_finish("answered", [GOOD])]])
    r = agent.ask("年假几天")
    assert r.status == "answered"
    assert "尚未用 read_doc 读取原文" in r.trace[1].observation
    assert [s.action for s in r.trace] == ["search_docs", "finish", "read_doc", "finish"]


def test_answered_without_citation_rejected():
    agent, _ = _agent([[_finish("answered", [])]], limits=Limits(max_steps=1))
    r = agent.ask("年假几天")
    assert r.status == "incomplete_step_limit"
    assert "必须提供至少一条引用" in r.trace[0].observation


def test_conflict_requires_two_docs():
    one = {"doc_id": "D01", "section_id": "S2", "quote": "30个自然日内"}
    agent, _ = _agent([[_read("D01", "S2")], [_finish("conflict_needs_confirmation", [one])]], limits=Limits(max_steps=2))
    r = agent.ask("报销期限")
    assert r.status == "incomplete_step_limit"
    assert "至少两份不同文档" in r.trace[1].observation


def test_step_limit_returns_explicit_incomplete():
    agent, _ = _agent([[_search("报销")], [_read("D01", "S2")], [_search("报销期限")]], limits=Limits(max_steps=2))
    r = agent.ask("报销期限")
    assert r.status == "incomplete_step_limit"
    assert r.steps_used == 2
    assert r.citations == []
    assert any("D01/S2" in g for g in r.gaps)  # 已读证据作为部分结果返回


def test_tool_call_limit_blocks_execution():
    agent, _ = _agent([[_search("a"), _search("b"), _search("c")], [_finish("out_of_scope")]], limits=Limits(max_tool_calls=2))
    r = agent.ask("x")
    assert r.tool_calls_used == 2
    assert "已达上限" in r.trace[2].observation
    assert r.status == "out_of_scope"


def test_tool_error_is_fed_back_not_raised():
    agent, model = _agent([[_search("年休假")], [_finish("tool_failed")]], faults=FaultInjection("search_docs", -1))
    r = agent.ask("年假几天")
    assert r.status == "tool_failed"
    assert r.trace[0].is_error
    assert model.seen_messages[1][-1]["content"][0]["is_error"] is True


def test_text_only_reply_is_nudged():
    agent, _ = _agent([[], [_finish("out_of_scope")]])
    r = agent.ask("x")
    assert r.trace[0].action == "(no_tool_call)"
    assert r.status == "out_of_scope"


def test_history_is_passed_to_model():
    agent, model = _agent([[_finish("out_of_scope")]])
    agent.ask("那一线城市呢？", [{"question": "出差住宿标准", "answer": "一线600元"}])
    contents = [m["content"] for m in model.seen_messages[0] if isinstance(m["content"], str)]
    assert any("出差住宿标准" in c for c in contents)

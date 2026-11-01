"""校验已提交的真实模型运行记录（examples/runs/anthropic/）。

不调用模型，只检查记录本身：判定为 PASS、引用逐字出自原文、步数不超上限、
两条对照路径的动作确实不同。用来防止 Trace 被手工改动后与代码行为不一致。
"""

import json
from pathlib import Path

import pytest

from kb_agent import Corpus, Limits

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / "examples" / "runs" / "anthropic"
RECORDS = sorted(RUN_DIR.glob("C*.json"))

pytestmark = pytest.mark.skipif(not RECORDS, reason="尚无真实模型运行记录")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", RECORDS, ids=[p.stem for p in RECORDS])
def test_record_is_consistent(path):
    rec = _load(path)
    res = rec["result"]
    assert rec["check"]["pass"], rec["check"]["reasons"]
    assert res["model"].startswith("claude-"), "记录应来自真实模型"
    assert res["steps_used"] <= rec["case"].get("max_steps", Limits.max_steps)

    corpus = Corpus.from_manifest(rec["case"]["corpus"])
    for c in res["citations"]:
        assert corpus.contains_quote(c["doc_id"], c["section_id"], c["quote"]), c

    for step in res["trace"]:
        if step["action"] in ("search_docs", "read_doc"):
            assert step["rationale"], f"第 {step['step']} 步缺少决策依据"


def test_two_paths_diverge_on_corpus_swap():
    a = _load(RUN_DIR / "C2_conflict.json")["result"]
    b = _load(RUN_DIR / "C3_conflict_removed.json")["result"]
    assert a["status"] == "conflict_needs_confirmation"
    assert b["status"] == "answered"
    assert [s["action"] for s in a["trace"]] != [s["action"] for s in b["trace"]]

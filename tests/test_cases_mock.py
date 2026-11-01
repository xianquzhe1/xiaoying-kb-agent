"""用离线 MockModel 跑 examples/cases.json 全部用例。

这里通过只说明流程链路（工具、校验、上限、对照语料）能跑通；
Mock 的决策是写死的规则，不能证明真实模型的决策效果。真实模型结果见 examples/runs/anthropic/。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_cases import check, run_case  # noqa: E402

CASES = json.loads((ROOT / "examples" / "cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case_with_mock(case):
    result, _ = run_case(case, "mock")
    verdict = check(case["expect"], result)
    assert verdict["pass"], verdict["reasons"]


def test_corpus_swap_changes_action():
    """同一问题，只替换语料，模型的结论动作随之改变。"""
    by_id = {c["id"]: c for c in CASES}
    with_d02, _ = run_case(by_id["C2_conflict"], "mock")
    without_d02, _ = run_case(by_id["C3_conflict_removed"], "mock")
    assert with_d02.status == "conflict_needs_confirmation"
    assert without_d02.status == "answered"

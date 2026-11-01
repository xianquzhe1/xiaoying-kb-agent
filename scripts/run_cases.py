"""批量运行 examples/cases.json，产出每个用例的实际输出与 Trace。

    python scripts/run_cases.py --provider anthropic      # 真实模型，写入 examples/runs/anthropic/
    python scripts/run_cases.py --provider mock           # 离线，写入 examples/runs/mock/
    python scripts/run_cases.py --provider anthropic --only C2_conflict C3_conflict_removed
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from kb_agent import FaultInjection, Limits, build_agent  # noqa: E402
from kb_agent.agent import AgentResult  # noqa: E402


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(ROOT / ".env")

    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["anthropic", "mock"], default="anthropic")
    p.add_argument("--only", nargs="*", help="只跑指定用例 ID")
    p.add_argument("--render-only", action="store_true", help="不调用模型，只从已有 JSON 重新生成 Markdown")
    args = p.parse_args()

    cases = json.loads((ROOT / "examples" / "cases.json").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]

    out_dir = ROOT / "examples" / "runs" / args.provider
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.render_only:
        for case in cases:
            path = out_dir / f"{case['id']}.json"
            if path.exists():
                record = json.loads(path.read_text(encoding="utf-8"))
                (out_dir / f"{case['id']}.md").write_text(render_md(record), encoding="utf-8")
        print(f"已从 JSON 重新生成 {out_dir.relative_to(ROOT)} 下的 Markdown")
        return 0

    rows = []
    for case in cases:
        result, history = run_case(case, args.provider)
        verdict = check(case["expect"], result)
        record = {
            "case": case,
            "provider": args.provider,
            "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "history": history,
            "result": result.to_dict(),
            "check": verdict,
        }
        (out_dir / f"{case['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / f"{case['id']}.md").write_text(render_md(record), encoding="utf-8")
        rows.append((case, result, verdict))
        mark = "PASS" if verdict["pass"] else "FAIL"
        print(f"[{mark}] {case['id']:<26} status={result.status:<28} steps={result.steps_used} tools={result.tool_calls_used}")
        for reason in verdict["reasons"]:
            print(f"        - {reason}")

    write_summary(out_dir, args.provider, rows)
    passed = sum(1 for _, _, v in rows if v["pass"])
    print(f"\n{passed}/{len(rows)} 通过，结果写入 {out_dir.relative_to(ROOT)}")
    return 0 if passed == len(rows) else 1


def run_case(case: dict, provider: str) -> tuple[AgentResult, list[dict]]:
    faults = None
    if case.get("fail_tools"):
        faults = FaultInjection(tuple(case["fail_tools"]), -1, message="知识库服务暂时不可用（模拟故障：存储节点离线）")
    elif case.get("fail_search"):
        faults = FaultInjection("search_docs", case["fail_search"])
    limits = Limits(max_steps=case.get("max_steps", Limits.max_steps))

    history: list[dict] = []
    # 追问用例：先真实跑一遍前序问题，用它的真实回答构成历史
    for q in case.get("history_questions", []):
        prev = build_agent(provider, case["corpus"]).ask(q, history)
        history.append({"question": q, "answer": prev.answer, "status": prev.status})

    agent = build_agent(provider, case["corpus"], faults, limits)
    result = agent.ask(case["question"], [{"question": h["question"], "answer": h["answer"]} for h in history])
    return result, history


def check(expect: dict, result: AgentResult) -> dict:
    """expect 可以是单个期望，也可以用 accept 列出多个可接受的结果（满足任一即通过）。"""
    cited = sorted({c["doc_id"] for c in result.citations})
    options = expect.get("accept") or [expect]
    all_reasons = []
    for i, opt in enumerate(options):
        reasons = []
        if result.status != opt["status"]:
            reasons.append(f"status 期望 {opt['status']}，实际 {result.status}")
        if sorted(opt["cite_docs"]) != cited:
            reasons.append(f"引用文档期望 {sorted(opt['cite_docs'])}，实际 {cited}")
        if opt.get("require_gaps") and not result.gaps:
            reasons.append("期望在 gaps 中声明未完成的部分，实际为空")
        if not reasons:
            return {"pass": True, "reasons": [], "cited_docs": cited, "matched_option": i}
        all_reasons.append(reasons)
    flat = all_reasons[0] if len(all_reasons) == 1 else [
        f"候选 {i + 1}：" + "；".join(r) for i, r in enumerate(all_reasons)
    ]
    return {"pass": False, "reasons": flat, "cited_docs": cited, "matched_option": None}


def render_md(record: dict) -> str:
    case, res = record["case"], record["result"]
    lines = [
        f"# {case['id']}",
        "",
        f"- 验收点：{'、'.join(case['acceptance'])}",
        f"- 场景：{case['scenario']}",
        f"- 语料：`{case['corpus']}`" + _fault_desc(case),
        f"- 模型：`{res['model']}`（provider={record['provider']}）",
        f"- 运行时间：{record['run_at']}",
        f"- 期望：{_expect_desc(case['expect'])}" + (f"（{case['expect']['note']}）" if case["expect"].get("note") else ""),
        f"- 判定：{'PASS' if record['check']['pass'] else 'FAIL'}"
        + ("" if record["check"]["pass"] else "（" + "；".join(record["check"]["reasons"]) + "）"),
        "",
    ]
    if record["history"]:
        lines += ["## 对话历史", ""]
        for h in record["history"]:
            lines += [f"- 问：{h['question']}", f"  答（{h['status']}）：{h['answer']}"]
        lines.append("")
    lines += [f"## 输入", "", f"> {case['question']}", "", "## Trace", "",
              "同一步内的多行是模型在一次回复中并行发起的调用。“后续动作”指模型看到该结果后的下一步决策。", "",
              "| 步 | 动作 | 参数 | 决策依据 | 返回摘要 | 后续动作 |", "|---|---|---|---|---|---|"]
    trace = res["trace"]
    for i, s in enumerate(trace):
        params = json.dumps(s["params"], ensure_ascii=False)
        if s["action"] == "finish":
            params = json.dumps({"status": s["params"].get("status")}, ensure_ascii=False)
        obs = ("**ERR** " if s["is_error"] else "") + s["observation"]
        nxt = next((t for t in trace[i + 1:] if t["step"] > s["step"]), None)
        if s["action"] == "finish" and not s["is_error"]:
            follow = "结束"
        elif nxt is None:
            follow = "触达上限，强制停止" if res["status"].startswith("incomplete") else "结束"
        else:
            follow = f"`{nxt['action']}`"
        lines.append(f"| {s['step']} | `{s['action']}` | {_cell(params)} | {_cell(s['rationale'])} | {_cell(obs, 180)} | {follow} |")
    lines += ["", "## 输出", "", f"- status：`{res['status']}`", f"- 步数 / 工具调用：{res['steps_used']} / {res['tool_calls_used']}",
              f"- 回答：{res['answer']}"]
    for c in res["citations"]:
        lines.append(f"- 引用 {c['doc_id']}/{c['section_id']}：「{c['quote']}」")
    for g in res["gaps"]:
        lines.append(f"- 缺口：{g}")
    lines.append(f"- tokens：in {res['usage']['input_tokens']} / out {res['usage']['output_tokens']}")
    return "\n".join(lines) + "\n"


def write_summary(out_dir: Path, provider: str, rows: list) -> None:
    lines = [f"# 运行汇总（provider={provider}）", "",
             "| 用例 | 验收点 | 期望 | 实际 | 步数 | 工具调用 | 判定 |", "|---|---|---|---|---|---|---|"]
    for case, res, v in rows:
        lines.append(
            f"| [{case['id']}]({case['id']}.md) | {'、'.join(case['acceptance'])} | {_expect_desc(case['expect'])} | "
            f"{res.status} | {res.steps_used} | {res.tool_calls_used} | {'PASS' if v['pass'] else 'FAIL'} |"
        )
    passed = sum(1 for _, _, v in rows if v["pass"])
    lines += ["", f"通过 {passed}/{len(rows)}。"]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fault_desc(case: dict) -> str:
    if case.get("fail_tools"):
        return f"；故障注入：{'、'.join(case['fail_tools'])} 持续失败"
    n = case.get("fail_search")
    if n == -1:
        return "；故障注入：search_docs 持续失败"
    if n:
        return f"；故障注入：search_docs 前 {n} 次失败"
    return ""


def _expect_desc(expect: dict) -> str:
    return " 或 ".join(o["status"] for o in expect.get("accept") or [expect])


def _cell(text: str, limit: int = 120) -> str:
    text = str(text).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


if __name__ == "__main__":
    raise SystemExit(main())

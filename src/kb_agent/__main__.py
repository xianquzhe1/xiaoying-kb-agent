"""CLI 入口。

    python -m kb_agent "报销期限多久？"
    python -m kb_agent "报销期限多久？" --corpus corpus_no_d02.json
    python -m kb_agent "出差住宿标准" --provider mock --fail-search 1
    python -m kb_agent --chat
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from . import FaultInjection, Limits, build_agent
from .agent import AgentResult
from .models import ModelError


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，统一改成 UTF-8 输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()

    p = argparse.ArgumentParser(prog="kb_agent", description="企业知识问答 Agent")
    p.add_argument("question", nargs="?", help="要问的问题；与 --chat 二选一")
    p.add_argument("--provider", choices=["anthropic", "mock"], default="anthropic",
                   help="anthropic=真实 Claude；mock=离线启发式，仅验证流程")
    p.add_argument("--corpus", default="corpus_full.json", help="data/ 下的语料清单文件")
    p.add_argument("--fail-search", type=int, default=0, metavar="N",
                   help="注入故障：前 N 次 search_docs 失败；-1 表示一直失败")
    p.add_argument("--max-steps", type=int, default=Limits.max_steps)
    p.add_argument("--max-tool-calls", type=int, default=Limits.max_tool_calls)
    p.add_argument("--json", action="store_true", help="输出完整 JSON（含 trace）")
    p.add_argument("--chat", action="store_true", help="多轮对话模式，支持上下文追问")
    args = p.parse_args(argv)

    if not args.question and not args.chat:
        p.error("请提供问题，或使用 --chat")

    faults = FaultInjection("search_docs", args.fail_search) if args.fail_search else None
    limits = Limits(args.max_steps, args.max_tool_calls)
    try:
        agent = build_agent(args.provider, args.corpus, faults, limits)
    except ModelError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2

    if not args.chat:
        result = agent.ask(args.question)
        _print(result, args.json)
        return 0

    history: list[dict] = []
    print("多轮模式，输入 exit 退出。")
    while True:
        try:
            q = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("exit", "quit", ""):
            break
        result = agent.ask(q, history)
        _print(result, args.json)
        history.append({"question": q, "answer": result.answer})
    return 0


def _print(result: AgentResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    for s in result.trace:
        flag = " [ERR]" if s.is_error else ""
        print(f"  #{s.step} {s.action}{flag} {json.dumps(s.params, ensure_ascii=False)}")
        if s.rationale:
            print(f"     依据: {s.rationale}")
        print(f"     结果: {s.observation[:160]}")
    print(f"\n状态: {result.status}  (步数 {result.steps_used}，工具调用 {result.tool_calls_used}，模型 {result.model})")
    print(f"回答: {result.answer}")
    for c in result.citations:
        print(f"  引用 {c['doc_id']}/{c['section_id']}: 「{c['quote']}」")
    for g in result.gaps:
        print(f"  缺口: {g}")


if __name__ == "__main__":
    raise SystemExit(main())

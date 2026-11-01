"""ReAct 主循环：模型选动作 → 执行工具 → 观察结果 → 模型决定下一步或结束。

代码只负责三件事，不替模型做业务决策：
1. 执行模型选的工具，把结果（含失败）原样回灌；
2. 校验 finish 的引用是否逐字出自原文，不通过就拒收并把原因回灌，让模型自己修正；
3. 步数 / 工具调用次数达到上限时强制停止，返回明确的未完成状态。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field

from .models import ModelClient, ModelError
from .tools import FINAL_STATUSES, FINISH, READ_DOC, SEARCH_DOCS, TOOL_SCHEMAS, ToolBox, ToolError

SYSTEM_PROMPT = """你是企业内部知识问答助手，帮员工从公司制度与产品 FAQ 中找到有依据的答案。

可用工具：search_docs（检索候选片段）、read_doc（读原文）、finish（给出结论并结束）。

工作方式：
- 回答必须有原文依据。检索摘要可能被截断，引用前先 read_doc 读原文；citations 的 quote 要逐字摘自原文。
- 检索结果为空或不相关时，判断是用词不一致还是资料确实没有：前者改用制度里可能出现的正式用词再检索，后者如实说明缺口。
- 多份资料对同一事项规定不同时，先看资料本身是否写明了适用范围或优先级。写明了就按资料说明作答并引用依据；没写明就用 conflict_needs_confirmation 列出各方原文，请用户确认适用哪一份，不要凭发布日期等推测替用户选。
- 工具报错时，根据错误信息决定重试、换参数还是换工具；持续失败就用 tool_failed 说明，不要编造结果。
- 用户追问时结合对话历史理解指代。问题本身指代不明且历史也无法补全时，用 need_clarification 追问。
- 每次调用工具都在 rationale 里用一句话写明依据了上一步的什么结果。
- 结束时必须调用 finish，不要只输出文字。"""


@dataclass
class Limits:
    max_steps: int = 8          # 模型决策轮数上限
    max_tool_calls: int = 10    # 业务工具（search/read）调用次数上限


@dataclass
class TraceStep:
    step: int
    action: str
    params: dict
    rationale: str
    observation: str
    is_error: bool
    elapsed_ms: int


@dataclass
class AgentResult:
    status: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    steps_used: int = 0
    tool_calls_used: int = 0
    trace: list[TraceStep] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})

    def to_dict(self) -> dict:
        data = asdict(self)
        data["trace"] = [asdict(s) for s in self.trace]
        return data


class KnowledgeAgent:
    def __init__(self, model: ModelClient, tools: ToolBox, limits: Limits | None = None) -> None:
        self.model = model
        self.tools = tools
        self.limits = limits or Limits()

    def ask(self, question: str, history: list[dict] | None = None) -> AgentResult:
        """history: 先前轮次的 [{"question": ..., "answer": ...}]，用于上下文追问。"""
        messages = _history_messages(history or [])
        messages.append({"role": "user", "content": f"用户问题：{question}"})

        trace: list[TraceStep] = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        biz_calls = 0
        served_by = ""
        evidence: list[dict] = []  # 已成功读取的原文，用于引用校验；触顶时作为部分结果返回

        for step in range(1, self.limits.max_steps + 1):
            try:
                turn = self.model.respond(SYSTEM_PROMPT, TOOL_SCHEMAS, messages)
            except ModelError as exc:
                return AgentResult(
                    status="model_error",
                    answer=f"模型调用失败，未能完成回答：{exc}",
                    gaps=["模型不可用，可使用 --provider mock 离线验证流程"],
                    steps_used=step - 1,
                    tool_calls_used=biz_calls,
                    trace=trace,
                    model=self.model.name,
                    usage=usage,
                )
            served_by = turn.model or self.model.name
            for k in usage:
                usage[k] += turn.usage.get(k, 0)

            messages.append({"role": "assistant", "content": turn.raw_content})

            if not turn.tool_calls:
                # 模型只输出了文字而没调用 finish：提醒一次，计入步数
                trace.append(_step(step, "(no_tool_call)", {}, "", turn.text[:200], True, 0))
                messages.append({"role": "user", "content": "请调用工具继续，或调用 finish 给出结论。"})
                continue

            results = []
            final: AgentResult | None = None
            for call in turn.tool_calls:
                params = {k: v for k, v in call.input.items() if k != "rationale"}
                rationale = str(call.input.get("rationale", ""))
                t0 = time.perf_counter()

                if call.name == FINISH:
                    read_keys = {(e["doc_id"], e["section_id"]) for e in evidence}
                    problem = self._validate_finish(params, read_keys)
                    ms = int((time.perf_counter() - t0) * 1000)
                    if problem:
                        trace.append(_step(step, FINISH, params, rationale, f"已拒收：{problem}", True, ms))
                        results.append(_tool_result(call.id, f"finish 未通过校验：{problem}请修正后重新提交。", True))
                        continue
                    trace.append(_step(step, FINISH, params, rationale, "已接受", False, ms))
                    final = AgentResult(
                        status=params["status"],
                        answer=params.get("answer", ""),
                        citations=params.get("citations") or [],
                        gaps=params.get("gaps") or [],
                    )
                    results.append(_tool_result(call.id, "已接受", False))
                    continue

                if call.name not in (SEARCH_DOCS, READ_DOC):
                    trace.append(_step(step, call.name, params, rationale, "未知工具", True, 0))
                    results.append(_tool_result(call.id, f"未知工具 {call.name}", True))
                    continue

                if biz_calls >= self.limits.max_tool_calls:
                    msg = f"工具调用次数已达上限 {self.limits.max_tool_calls}，本次未执行。请基于已有结果调用 finish。"
                    trace.append(_step(step, call.name, params, rationale, msg, True, 0))
                    results.append(_tool_result(call.id, msg, True))
                    continue

                biz_calls += 1
                try:
                    obs = self.tools.run(call.name, call.input)
                    is_error = False
                except ToolError as exc:
                    obs = str(exc)
                    is_error = True
                ms = int((time.perf_counter() - t0) * 1000)
                trace.append(_step(step, call.name, params, rationale, _summarize(call.name, obs, is_error), is_error, ms))
                results.append(_tool_result(call.id, obs, is_error))
                if call.name == READ_DOC and not is_error:
                    data = json.loads(obs)
                    evidence.append({"doc_id": data["doc_id"], "section_id": data["section_id"], "heading": data["heading"]})

            if final is not None:
                final.steps_used = step
                final.tool_calls_used = biz_calls
                final.trace = trace
                final.model = served_by
                final.usage = usage
                return final

            messages.append({"role": "user", "content": results})

        # 达到步数上限仍未收口：明确返回未完成，不把半成品包装成答案
        return AgentResult(
            status="incomplete_step_limit",
            answer=f"已达到执行步数上限（{self.limits.max_steps} 步），未能形成有依据的结论。",
            citations=[],
            gaps=["未完成：步数耗尽"] + [f"已读取但未形成结论：{e['doc_id']}/{e['section_id']} {e['heading']}" for e in evidence],
            steps_used=self.limits.max_steps,
            tool_calls_used=biz_calls,
            trace=trace,
            model=served_by,
            usage=usage,
        )

    def _validate_finish(self, params: dict, read_keys: set[tuple[str, str]]) -> str | None:
        status = params.get("status")
        if status not in FINAL_STATUSES:
            return f"status 必须是 {FINAL_STATUSES} 之一。"
        citations = params.get("citations") or []
        if status in ("answered", "conflict_needs_confirmation") and not citations:
            return f"status={status} 时必须提供至少一条引用。"
        if status == "conflict_needs_confirmation" and len({c.get("doc_id") for c in citations}) < 2:
            return "判定为冲突时，引用须来自至少两份不同文档。"
        # 摘要可能被截断，只看摘要就引用会断章取义，所以要求引用的片段本轮读过原文
        unread = sorted({f"{c.get('doc_id')}/{c.get('section_id')}" for c in citations
                         if (c.get("doc_id"), c.get("section_id")) not in read_keys})
        if unread:
            return "以下片段尚未用 read_doc 读取原文，不能引用：" + "、".join(unread) + "。请先读取或删去这些引用。"
        bad = []
        for c in citations:
            if not self.tools.corpus.contains_quote(c.get("doc_id", ""), c.get("section_id", ""), c.get("quote", "")):
                bad.append(f"{c.get('doc_id')}/{c.get('section_id')} 的 quote「{c.get('quote', '')[:40]}」")
        if bad:
            return "以下引用未在对应原文中逐字找到：" + "；".join(bad) + "。"
        return None


def _history_messages(history: list[dict]) -> list[dict]:
    messages: list[dict] = []
    for turn in history:
        messages.append({"role": "user", "content": f"用户问题：{turn['question']}"})
        messages.append({"role": "assistant", "content": f"（上一轮回答）{turn['answer']}"})
    return messages


def _tool_result(tool_use_id: str, content: str, is_error: bool) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _step(step: int, action: str, params: dict, rationale: str, obs: str, is_error: bool, ms: int) -> TraceStep:
    return TraceStep(step=step, action=action, params=params, rationale=rationale, observation=obs, is_error=is_error, elapsed_ms=ms)


def _summarize(tool: str, obs: str, is_error: bool) -> str:
    if is_error:
        return f"ERROR: {obs}"
    data = json.loads(obs)
    if tool == SEARCH_DOCS:
        if not data["hits"]:
            return f"0 命中（query={data['query']}）"
        return f"{data['hit_count']} 命中：" + "；".join(
            f"{h['doc_id']}/{h['section_id']} {h['heading']}({h['version_date']}, score={h['score']})" for h in data["hits"]
        )
    if tool == READ_DOC:
        return f"{data['doc_id']}/{data['section_id']}《{data['doc_title']}》{data['heading']}：{data['text']}"
    return obs[:200]

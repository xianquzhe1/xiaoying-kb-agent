"""模型适配层。

Agent 循环只依赖 `ModelClient.respond(system, tools, messages) -> ModelTurn`。
- AnthropicModel：真实 Claude，经 Messages API 工具调用驱动决策。
- MockModel：离线启发式策略，只用来验证流程能跑通，不代表真实模型的决策能力。
- ScriptedModel：测试用，按预设回复逐条返回，用于验证循环的护栏（步数上限、引用校验等）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .tools import FINISH, READ_DOC, SEARCH_DOCS


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ModelTurn:
    tool_calls: list[ToolCall]
    text: str
    stop_reason: str
    # 原样回传给下一次请求的 assistant content（真实模型需保留 thinking 等块）
    raw_content: Any
    usage: dict = field(default_factory=dict)
    model: str = ""


class ModelClient(Protocol):
    name: str

    def respond(self, system: str, tools: list[dict], messages: list[dict]) -> ModelTurn: ...


class ModelError(RuntimeError):
    """模型调用失败（网络、鉴权、限流、拒答等），区别于业务工具失败。"""


# --------------------------------------------------------------------------- #
# 真实模型
# --------------------------------------------------------------------------- #
class AnthropicModel:
    def __init__(
        self,
        model: str | None = None,
        effort: str | None = None,
        use_fallbacks: bool | None = None,
        max_tokens: int = 16000,
    ) -> None:
        import anthropic

        self._anthropic = anthropic
        self.model = model or os.getenv("KB_AGENT_MODEL", "claude-opus-5-5")
        self.effort = effort or os.getenv("KB_AGENT_EFFORT", "medium")
        if use_fallbacks is None:
            use_fallbacks = os.getenv("KB_AGENT_FALLBACKS", "default").lower() != "off"
        self.use_fallbacks = use_fallbacks
        self.max_tokens = max_tokens
        self.name = f"anthropic:{self.model}"

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ModelError("未设置 ANTHROPIC_API_KEY。请复制 .env.example 为 .env 并填写，或使用 --provider mock。")
        # 显式传 key，避免 SDK 回落到环境里其他来源的凭证（如 ANTHROPIC_AUTH_TOKEN）
        self.base_url = os.getenv("ANTHROPIC_BASE_URL") or None
        self.client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            base_url=self.base_url,
            timeout=120.0,
            max_retries=2,
        )

    def respond(self, system: str, tools: list[dict], messages: list[dict]) -> ModelTurn:
        anthropic = self._anthropic
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": self.effort},
        )
        try:
            if self.use_fallbacks:
                # 安全分类器误拒时由服务端自动切换到备用模型重跑
                response = self.client.beta.messages.create(
                    **kwargs,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            else:
                response = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise ModelError(f"鉴权失败：{exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise ModelError(f"触发限流：{exc.message}") from exc
        except anthropic.BadRequestError as exc:
            raise ModelError(f"请求参数错误：{exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"API 错误 {exc.status_code}：{exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError(f"网络连接失败：{exc}") from exc

        if response.stop_reason == "refusal":
            raise ModelError("模型拒绝回答（refusal）")

        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        text = "".join(b.text for b in response.content if b.type == "text")
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        return ModelTurn(
            tool_calls=tool_calls,
            text=text,
            stop_reason=response.stop_reason or "",
            raw_content=response.content,
            usage=usage,
            model=response.model,
        )


# --------------------------------------------------------------------------- #
# 离线 Mock：读取工具结果后按固定启发式选下一步
# --------------------------------------------------------------------------- #
_DAYS = re.compile(r"(\d+)个自然日内")
_QUERY_REWRITES = {
    "酒店": "住宿费",
    "睡觉": "住宿费",
    "房费": "住宿费",
    "报账": "报销",
    "年假": "年休假",
}
_GENERIC = ("这个", "那个", "这事", "怎么办")


class MockModel:
    """确定性的离线策略。

    它确实会读 observation 再选动作（检索为空则改写、失败则重试一次、读到期限数字不一致则判冲突），
    但这些都是写死的规则，不能证明真实模型的决策质量。只用于离线跑通流程和 CI 测试。
    """

    name = "mock:heuristic"

    def __init__(self) -> None:
        self._n = 0

    def respond(self, system: str, tools: list[dict], messages: list[dict]) -> ModelTurn:
        state = _MockState.from_messages(messages)
        call = self._decide(state)
        self._n += 1
        call_id = f"mock_{self._n}"
        raw = [{"type": "tool_use", "id": call_id, "name": call["name"], "input": call["input"]}]
        return ModelTurn(
            tool_calls=[ToolCall(id=call_id, name=call["name"], input=call["input"])],
            text="",
            stop_reason="tool_use",
            raw_content=raw,
            model=self.name,
        )

    def _decide(self, s: "_MockState") -> dict:
        q = s.question
        if s.last_finish_rejected:
            return _finish("tool_failed", "引用校验未通过，Mock 策略不做二次修正。", [], [s.last_finish_rejected])

        if not s.searches:
            if len(q) <= 6 and any(g in q for g in _GENERIC) and not s.history_text:
                return _finish("need_clarification", "问题缺少具体对象，请说明要咨询的制度或产品功能。", [], ["咨询对象不明确"])
            return _call(SEARCH_DOCS, query=_mock_query(q, s.history_text), rationale="首轮检索")

        last = s.searches[-1]
        if last["error"]:
            failures = sum(1 for x in s.searches if x["error"])
            if failures >= 2:
                return _finish("tool_failed", "检索工具连续失败，暂无法取证，请稍后重试。", [], ["search_docs 连续失败"])
            return _call(SEARCH_DOCS, query=last["query"], rationale="上次检索报错，原参数重试一次")

        # 相关度门槛：只命中一个泛词（如"公司"）的结果视同未命中
        hits = [h for h in last["hits"] if h["score"] >= 2]
        if not hits:
            rewritten = _rewrite(last["query"])
            tried = {x["query"] for x in s.searches}
            if rewritten and rewritten not in tried:
                return _call(SEARCH_DOCS, query=rewritten, rationale="零命中，改写为制度用词")
            return _finish("out_of_scope", "现有制度与 FAQ 中没有找到相关资料。", [], [f"语料未覆盖：{q}"])

        # 读取得分最高的若干片段（每个文档至多一个，最多两个）
        best = hits[0]["score"]
        wanted: list[tuple[str, str]] = []
        for h in hits:
            if h["score"] < best * 0.6:
                continue
            if h["doc_id"] in {d for d, _ in wanted}:
                continue
            wanted.append((h["doc_id"], h["section_id"]))
            if len(wanted) == 2:
                break
        for key in wanted:
            if key not in s.reads:
                return _call(READ_DOC, doc_id=key[0], section_id=key[1], rationale="读取候选原文")

        texts = [(key, s.reads[key]) for key in wanted]
        day_values = {}
        for key, text in texts:
            m = _DAYS.search(text)
            if m:
                day_values[key] = m.group(0)
        if len(set(day_values.values())) >= 2:
            cites = [{"doc_id": k[0], "section_id": k[1], "quote": v} for k, v in day_values.items()]
            return _finish(
                "conflict_needs_confirmation",
                "检索到的资料对期限规定不一致：" + "；".join(f"{k[0]} 写明{v}" for k, v in day_values.items()) + "。请确认适用哪份制度。",
                cites,
                ["请确认适用的制度版本或范围"],
            )

        key, text = texts[0]
        quote = text.split("。")[0]
        return _finish("answered", f"依据 {key[0]}：{quote}。", [{"doc_id": key[0], "section_id": key[1], "quote": quote}], [])


def _call(name: str, rationale: str, **kwargs: Any) -> dict:
    return {"name": name, "input": {**kwargs, "rationale": rationale}}


def _finish(status: str, answer: str, citations: list[dict], gaps: list[str]) -> dict:
    return {
        "name": FINISH,
        "input": {"status": status, "answer": answer, "citations": citations, "gaps": gaps, "rationale": "Mock 规则收口"},
    }


def _mock_query(question: str, history: str) -> str:
    # 首轮用原话检索（短追问拼上历史问题补全指代），零命中后再由 _rewrite 改写
    if history and len(question) <= 10:
        return history + " " + question
    return question


def _rewrite(query: str) -> str | None:
    out = query
    for src, dst in _QUERY_REWRITES.items():
        out = out.replace(src, dst)
    return out if out != query else None


@dataclass
class _MockState:
    question: str
    history_text: str
    searches: list[dict]
    reads: dict[tuple[str, str], str]
    last_finish_rejected: str | None

    @classmethod
    def from_messages(cls, messages: list[dict]) -> "_MockState":
        # 最后一条纯文本 user 消息是当前问题，之前的是历史
        user_texts = [m["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], str)]
        question = _strip_question(user_texts[-1]) if user_texts else ""
        history = " ".join(_strip_question(t) for t in user_texts[:-1])

        pending: dict[str, dict] = {}
        searches: list[dict] = []
        reads: dict[tuple[str, str], str] = {}
        rejected: str | None = None
        for m in messages:
            if m["role"] == "assistant" and isinstance(m["content"], list):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        pending[b["id"]] = b
            if m["role"] == "user" and isinstance(m["content"], list):
                for b in m["content"]:
                    if b.get("type") != "tool_result":
                        continue
                    call = pending.get(b["tool_use_id"])
                    if not call:
                        continue
                    content = b["content"]
                    if call["name"] == SEARCH_DOCS:
                        if b.get("is_error"):
                            searches.append({"query": call["input"]["query"], "error": True, "hits": []})
                        else:
                            data = json.loads(content)
                            searches.append({"query": call["input"]["query"], "error": False, "hits": data["hits"]})
                    elif call["name"] == READ_DOC and not b.get("is_error"):
                        data = json.loads(content)
                        reads[(data["doc_id"], data["section_id"])] = data["text"]
                    elif call["name"] == FINISH and b.get("is_error"):
                        rejected = content
        return cls(question, history, searches, reads, rejected)


def _strip_question(text: str) -> str:
    marker = "用户问题："
    return text.split(marker, 1)[1].strip() if marker in text else text.strip()


# --------------------------------------------------------------------------- #
# 测试用脚本模型
# --------------------------------------------------------------------------- #
class ScriptedModel:
    """按顺序返回预设的工具调用，用于在不依赖任何模型的情况下测试循环护栏。"""

    name = "scripted"

    def __init__(self, script: list[list[dict]]) -> None:
        self.script = list(script)
        self.seen_messages: list[list[dict]] = []
        self._n = 0

    def respond(self, system: str, tools: list[dict], messages: list[dict]) -> ModelTurn:
        self.seen_messages.append(list(messages))
        if not self.script:
            return ModelTurn(tool_calls=[], text="(脚本已用完)", stop_reason="end_turn", raw_content=[{"type": "text", "text": "(脚本已用完)"}])
        calls = self.script.pop(0)
        tool_calls, raw = [], []
        for c in calls:
            self._n += 1
            cid = f"s_{self._n}"
            tool_calls.append(ToolCall(id=cid, name=c["name"], input=c["input"]))
            raw.append({"type": "tool_use", "id": cid, "name": c["name"], "input": c["input"]})
        if not raw:
            raw = [{"type": "text", "text": "不调用工具"}]
        return ModelTurn(tool_calls=tool_calls, text="", stop_reason="tool_use" if tool_calls else "end_turn", raw_content=raw)

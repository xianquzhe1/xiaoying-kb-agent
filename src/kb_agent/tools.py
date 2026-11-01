"""工具定义、分发与故障注入。

工具返回的 observation 一律是"事实 + 可操作提示"，不含结论性判断。
例如检索为空时返回命中数 0 与可用文档清单，由模型决定是改写 query、换方向还是收口。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .corpus import Corpus

SEARCH_DOCS = "search_docs"
READ_DOC = "read_doc"
FINISH = "finish"

FINAL_STATUSES = (
    "answered",
    "conflict_needs_confirmation",
    "need_clarification",
    "out_of_scope",
    "tool_failed",
)

_RATIONALE = {
    "type": "string",
    "description": "一句话说明为什么选这个动作、依据了上一步的哪个结果。写入运行记录供审计，不要写长篇推理。",
}

TOOL_SCHEMAS = [
    {
        "name": SEARCH_DOCS,
        "description": (
            "按关键词检索企业制度与 FAQ 语料，返回候选片段的元数据与摘要。"
            "摘要可能被截断，不足以作为回答依据；要引用必须先用 read_doc 取原文。"
            "命中为空时说明关键词与语料用词不一致，可改写 query 后重试。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词，建议使用制度里可能出现的正式用词。"},
                "top_k": {"type": "integer", "description": "返回条数，1-5，默认 3。"},
                "rationale": _RATIONALE,
            },
            "required": ["query", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": READ_DOC,
        "description": "读取指定文档片段的完整原文。引用前必须调用，逐字引用须来自这里返回的 text。",
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "文档 ID，如 D01。"},
                "section_id": {"type": "string", "description": "片段 ID，如 S2。"},
                "rationale": _RATIONALE,
            },
            "required": ["doc_id", "section_id", "rationale"],
            "additionalProperties": False,
        },
    },
    {
        "name": FINISH,
        "description": (
            "结束本轮并给出结论。status 取值：\n"
            "- answered：资料充分且无冲突，给出答案与逐字引用。\n"
            "- conflict_needs_confirmation：两份及以上资料规定冲突且无法从资料本身判定优先级，"
            "须列出冲突各方引用并向用户提出确认问题，不得自行选一个答案。\n"
            "- need_clarification：问题本身指代不明或缺少必要条件，无法检索。\n"
            "- out_of_scope：语料中没有相关资料。\n"
            "- tool_failed：工具持续失败导致无法取证。\n"
            "citations 中每条 quote 必须逐字出自对应片段原文，系统会校验，校验失败会要求重新提交。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": list(FINAL_STATUSES)},
                "answer": {"type": "string", "description": "给用户的回答；冲突或缺资料时说明情况。"},
                "citations": {
                    "type": "array",
                    "description": "引用列表。answered 与 conflict_needs_confirmation 必须非空。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "doc_id": {"type": "string"},
                            "section_id": {"type": "string"},
                            "quote": {"type": "string", "description": "逐字摘录的原文片段。"},
                        },
                        "required": ["doc_id", "section_id", "quote"],
                        "additionalProperties": False,
                    },
                },
                "gaps": {
                    "type": "array",
                    "description": "缺口或待用户确认的问题。",
                    "items": {"type": "string"},
                },
                "rationale": _RATIONALE,
            },
            "required": ["status", "answer", "rationale"],
            "additionalProperties": False,
        },
    },
]


class ToolError(RuntimeError):
    """工具执行失败。会以 is_error 的 tool_result 回灌给模型。"""


@dataclass
class FaultInjection:
    """故障注入配置。用于构造"工具失败"验收场景。

    fail_tool: 目标工具名，或多个工具名组成的元组；fail_times: 连续失败次数，之后恢复正常。
    fail_times=-1 表示永久失败。
    """

    fail_tool: str | tuple[str, ...] | None = None
    fail_times: int = 0
    message: str = "检索服务暂时不可用（模拟故障：索引节点超时）"
    _used: int = field(default=0, repr=False)

    def should_fail(self, tool_name: str) -> bool:
        targets = (self.fail_tool,) if isinstance(self.fail_tool, str) else (self.fail_tool or ())
        if tool_name not in targets:
            return False
        if self.fail_times == -1:
            self._used += 1
            return True
        if self._used < self.fail_times:
            self._used += 1
            return True
        return False


class ToolBox:
    def __init__(self, corpus: Corpus, faults: FaultInjection | None = None) -> None:
        self.corpus = corpus
        self.faults = faults or FaultInjection()
        self.call_count = 0

    def run(self, name: str, payload: dict) -> str:
        """执行工具，返回给模型看的 observation 文本。失败抛 ToolError。"""
        self.call_count += 1

        if self.faults.should_fail(name):
            raise ToolError(
                f"{self.faults.message}。本次 {name} 调用未返回任何结果，"
                "可稍后重试、调整参数，或改用其他工具取证。"
            )

        if name == SEARCH_DOCS:
            return self._search(payload)
        if name == READ_DOC:
            return self._read(payload)
        raise ToolError(f"未知工具 {name}")

    def _search(self, payload: dict) -> str:
        query = (payload.get("query") or "").strip()
        if not query:
            raise ToolError("query 不能为空")
        top_k = payload.get("top_k") or 3
        top_k = max(1, min(5, int(top_k)))

        hits = self.corpus.search(query, top_k=top_k)
        if not hits:
            inventory = sorted({(s.doc_id, s.doc_title) for s in self.corpus.sections})
            listing = "、".join(f"{doc_id}《{title}》" for doc_id, title in inventory)
            return json.dumps(
                {
                    "query": query,
                    "hit_count": 0,
                    "hits": [],
                    "corpus_inventory": listing,
                },
                ensure_ascii=False,
            )
        return json.dumps({"query": query, "hit_count": len(hits), "hits": hits}, ensure_ascii=False)

    def _read(self, payload: dict) -> str:
        doc_id = (payload.get("doc_id") or "").strip()
        section_id = (payload.get("section_id") or "").strip()
        if not doc_id or not section_id:
            raise ToolError("doc_id 与 section_id 均为必填")
        try:
            section = self.corpus.read(doc_id, section_id)
        except KeyError as exc:
            raise ToolError(str(exc)) from exc
        return json.dumps(section, ensure_ascii=False)

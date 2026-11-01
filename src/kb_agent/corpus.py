"""语料加载与检索工具。

工具层只做事实性检索与读取，不做"是否冲突""该用哪一份"之类的判断——
这些判断留给模型，否则 ReAct 循环会退化成代码里的固定分支。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# 轻量同义词表：让"住宿/酒店""报销期限/报账时限"这类表述能命中。
# 刻意保持很小，使得部分提问首检索必然不中，模型需要自己改写 query 再试。
SYNONYMS: dict[str, list[str]] = {
    "报账": ["报销"],
    "时限": ["期限"],
    "年假": ["年休假"],
    "补贴": ["补助"],
    "机票": ["航班"],
}

STOPWORDS = set("的 了 吗 呢 是 在 和 与 及 请问 我 们 一下 多少 多久 怎么 如何 什么 可以 能 要 有 个 吧 呀 对 啊".split())


@dataclass(frozen=True)
class Section:
    doc_id: str
    doc_title: str
    doc_type: str
    version_date: str
    owner: str
    section_id: str
    heading: str
    text: str


class Corpus:
    """内存语料库。manifest 决定装入哪些文档，便于做"移除某文档"的对照实验。"""

    def __init__(self, sections: list[Section], corpus_id: str) -> None:
        self.sections = sections
        self.corpus_id = corpus_id

    @classmethod
    def from_manifest(cls, manifest_path: str | Path) -> "Corpus":
        manifest_path = Path(manifest_path)
        if not manifest_path.is_absolute():
            manifest_path = DATA_DIR / manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        sections: list[Section] = []
        for rel in manifest["docs"]:
            doc = json.loads((DATA_DIR / rel).read_text(encoding="utf-8"))
            for sec in doc["sections"]:
                sections.append(
                    Section(
                        doc_id=doc["doc_id"],
                        doc_title=doc["title"],
                        doc_type=doc["doc_type"],
                        version_date=doc["version_date"],
                        owner=doc["owner"],
                        section_id=sec["section_id"],
                        heading=sec["heading"],
                        text=sec["text"],
                    )
                )
        return cls(sections, manifest["corpus_id"])

    # ---- 工具 1：检索 ----
    def search(self, query: str, top_k: int = 3) -> list[dict]:
        terms = _tokenize(query)
        scored: list[tuple[float, Section, list[str]]] = []
        for sec in self.sections:
            score, matched = _score(terms, sec)
            if score > 0:
                scored.append((score, sec, matched))
        scored.sort(key=lambda item: (-item[0], item[1].doc_id, item[1].section_id))

        return [
            {
                "doc_id": sec.doc_id,
                "doc_title": sec.doc_title,
                "doc_type": sec.doc_type,
                "version_date": sec.version_date,
                "owner": sec.owner,
                "section_id": sec.section_id,
                "heading": sec.heading,
                "snippet": sec.text[:80] + ("…" if len(sec.text) > 80 else ""),
                "score": round(score, 3),
                "matched_terms": matched,
            }
            for score, sec, matched in scored[:top_k]
        ]

    # ---- 工具 2：读原文 ----
    def read(self, doc_id: str, section_id: str) -> dict:
        for sec in self.sections:
            if sec.doc_id == doc_id and sec.section_id == section_id:
                return {
                    "doc_id": sec.doc_id,
                    "doc_title": sec.doc_title,
                    "doc_type": sec.doc_type,
                    "version_date": sec.version_date,
                    "owner": sec.owner,
                    "section_id": sec.section_id,
                    "heading": sec.heading,
                    "text": sec.text,
                }
        available = sorted(
            {f"{s.doc_id}/{s.section_id}" for s in self.sections if s.doc_id == doc_id}
        )
        raise KeyError(
            f"未找到 {doc_id}/{section_id}。"
            + (f"该文档可用片段：{', '.join(available)}" if available else f"语料中没有文档 {doc_id}")
        )

    def contains_quote(self, doc_id: str, section_id: str, quote: str) -> bool:
        """引用校验：quote 必须逐字出现在该片段原文中。"""
        try:
            section = self.read(doc_id, section_id)
        except KeyError:
            return False
        return _normalize(quote) in _normalize(section["text"])


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _tokenize(query: str) -> list[str]:
    raw = re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", query)
    terms: list[str] = []
    for chunk in raw:
        if re.match(r"[a-zA-Z0-9]+", chunk):
            terms.append(chunk.lower())
            continue
        # 中文按 2-3 字滑窗切，近似 n-gram 检索
        for size in (3, 2):
            for i in range(len(chunk) - size + 1):
                gram = chunk[i : i + size]
                if gram not in STOPWORDS:
                    terms.append(gram)
        if len(chunk) == 1 and chunk not in STOPWORDS:
            terms.append(chunk)
    expanded = list(terms)
    for term in terms:
        expanded.extend(SYNONYMS.get(term, []))
    return expanded


def _score(terms: list[str], sec: Section) -> tuple[float, list[str]]:
    haystack = sec.heading + sec.text
    score = 0.0
    seen: set[str] = set()
    matched: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        if term in sec.heading:
            score += 2.0
            matched.append(term)
        elif term in haystack:
            score += 1.0
            matched.append(term)
    return score, matched

"""企业知识问答 Agent（题3）。"""

from .agent import AgentResult, KnowledgeAgent, Limits
from .corpus import Corpus
from .tools import FaultInjection, ToolBox

__all__ = ["AgentResult", "Corpus", "FaultInjection", "KnowledgeAgent", "Limits", "ToolBox", "build_agent"]


def build_agent(
    provider: str = "anthropic",
    corpus: str = "corpus_full.json",
    faults: FaultInjection | None = None,
    limits: Limits | None = None,
) -> KnowledgeAgent:
    from .models import AnthropicModel, MockModel

    model = AnthropicModel() if provider == "anthropic" else MockModel()
    return KnowledgeAgent(model, ToolBox(Corpus.from_manifest(corpus), faults), limits)

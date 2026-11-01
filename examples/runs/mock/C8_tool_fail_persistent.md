# C8_tool_fail_persistent

- 验收点：工具失败
- 场景：工具失败（检索持续不可用，读取工具可用）
- 语料：`corpus_full.json`；故障注入：search_docs 持续失败
- 模型：`mock:heuristic`（provider=mock）
- 运行时间：2026-11-01T08:19:52+00:00
- 期望：tool_failed 或 answered（两种结果都合格：放弃并说明失败；或换用 read_doc 直接取证，答案有据且在 gaps 中声明检索未完成。）
- 判定：PASS

## 输入

> 会员能导出4K吗？

## Trace

同一步内的多行是模型在一次回复中并行发起的调用。“后续动作”指模型看到该结果后的下一步决策。

| 步 | 动作 | 参数 | 决策依据 | 返回摘要 | 后续动作 |
|---|---|---|---|---|---|
| 1 | `search_docs` | {"query": "会员能导出4K吗？"} | 首轮检索 | **ERR** ERROR: 检索服务暂时不可用（模拟故障：索引节点超时）。本次 search_docs 调用未返回任何结果，可稍后重试、调整参数，或改用其他工具取证。 | `search_docs` |
| 2 | `search_docs` | {"query": "会员能导出4K吗？"} | 上次检索报错，原参数重试一次 | **ERR** ERROR: 检索服务暂时不可用（模拟故障：索引节点超时）。本次 search_docs 调用未返回任何结果，可稍后重试、调整参数，或改用其他工具取证。 | `finish` |
| 3 | `finish` | {"status": "tool_failed"} | Mock 规则收口 | 已接受 | 结束 |

## 输出

- status：`tool_failed`
- 步数 / 工具调用：3 / 2
- 回答：检索工具连续失败，暂无法取证，请稍后重试。
- 缺口：search_docs 连续失败
- tokens：in 0 / out 0

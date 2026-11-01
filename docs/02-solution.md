# 02 方案

## 调用流程

```
用户问题 (+ 历史)
     │
     ▼
┌──────────────────────────────────────────────────┐
│ for step in 1..max_steps:                        │
│   turn = model.respond(system, tools, messages)  │  ← 模型选动作
│   for call in turn.tool_calls:                   │
│     search_docs / read_doc  → ToolBox.run()      │  ← 执行工具
│        成功 → tool_result                         │
│        失败 → tool_result(is_error=True)          │
│        超过 max_tool_calls → 拒绝执行并告知        │
│     finish → _validate_finish()                  │
│        通过 → 返回结果                            │
│        不通过 → tool_result(is_error=True, 原因)  │
│   messages += tool_results                       │  ← 观察结果，回灌给模型
└──────────────────────────────────────────────────┘
     │ 循环结束仍未 finish
     ▼
status = incomplete_step_limit（附已读证据）
```

代码在 [src/kb_agent/agent.py](../src/kb_agent/agent.py)。

## 动作空间

| 动作 | 参数 | 返回 |
|---|---|---|
| `search_docs` | `query`、`top_k`、`rationale` | 候选片段：doc_id、标题、版本日期、负责部门、section_id、标题、80 字摘要、得分、命中词。零命中时附语料清单 |
| `read_doc` | `doc_id`、`section_id`、`rationale` | 片段完整原文和元数据。找不到时报错，并列出该文档可用的片段 |
| `finish` | `status`、`answer`、`citations`、`gaps`、`rationale` | 通过校验则结束；不通过则返回原因，模型可修正后重新提交 |

每个动作都要求模型填 `rationale`，用一句话说明依据了上一步的什么结果。这个字段写进 Trace，用来满足"简要决策依据"的要求。它是模型主动输出的说明，不是模型内部思维过程。

## 动态决策在哪里

题目要求排除"固定条件分支、自动重试、预设决策日志"。下面每一项决策都由模型根据观察结果做出，代码里没有对应的 if 分支：

| 观察到的结果 | 模型可能的动作 | 在哪个 Trace 里能看到 |
|---|---|---|
| 检索命中两份文档，原文期限不同 | 再检索一次，找 D02 有没有写明效力或优先级；找不到就判冲突 | [C2](../examples/runs/anthropic/C2_conflict.md) 第 3 步 |
| 只命中 D01 一份 | 直接作答，或换词再检索确认没有其他规定 | [C3](../examples/runs/anthropic/C3_conflict_removed.md) |
| 检索零命中 | 结合语料清单判断是用词不一致还是资料确实没有 | [C4](../examples/runs/anthropic/C4_out_of_scope.md) 第 2 步 |
| 口语化提问 | 首次检索就改用制度用词（"住宿费 标准"） | [C5](../examples/runs/anthropic/C5_paraphrase.md) 第 1 步 |
| 追问"那一线城市呢" | 从历史中补全指代，检索"差旅费用标准 住宿 一线城市" | [C6](../examples/runs/anthropic/C6_followup.md) 第 1 步 |
| 检索报错 | 换参数重试；仍失败就改用 read_doc；再不行就收口 | [C7](../examples/runs/anthropic/C7_tool_fail_recover.md)、[C8](../examples/runs/anthropic/C8_tool_fail_persistent.md)、[C10](../examples/runs/anthropic/C10_all_tools_fail.md) |

**两条对照路径**（题目要求"体现模型根据不同工具结果调整动作"）：

C2 和 C3 问的是同一个问题"报销期限多久？"，代码、提示词、参数完全相同，唯一区别是 C3 的语料去掉了 D02。

- C2：检索返回 D01、D02 → 并行读两份原文 → 发现 30 天和 15 天冲突 → 再检索 D02 是否写明替代关系 → 没找到 → `conflict_needs_confirmation`
- C3：检索只返回 D01 → 读 D01 原文，同时换词再检索确认没有其他规定 → 读 D01 适用范围 → `answered`

两条路径的分叉完全来自工具返回的内容。[tests/test_recorded_runs.py](../tests/test_recorded_runs.py) 的 `test_two_paths_diverge_on_corpus_swap` 检查两条 Trace 的动作序列确实不同。

## 代码层做了什么、没做什么

代码层只做三件事，都是硬约束：

1. **执行工具并原样回灌结果。** 成功、失败、零命中都作为 observation 交给模型。失败时用 `is_error=True`，不在代码里重试。
2. **校验 finish。** 规则如下，任何一条不满足就拒收，并把原因作为 observation 回灌，让模型自己修正：
   - `status` 必须是合法值
   - `answered` 和 `conflict_needs_confirmation` 必须带引用
   - `conflict_needs_confirmation` 的引用必须来自至少两份不同文档
   - 引用的片段必须在本轮用 `read_doc` 读过（摘要会被截断，只看摘要就引用容易断章取义）
   - 每条 quote 必须逐字出现在对应片段原文中
3. **强制上限。** `max_steps=8`（模型决策轮数）、`max_tool_calls=10`（search 和 read 的总调用次数）。工具调用超限时拒绝执行并告诉模型；步数用完时直接返回 `incomplete_step_limit`，并在 gaps 中列出已读取的片段。

代码层**不做**的事：

- 不判断是否冲突。工具只返回各文档的原文和版本日期。冲突判断如果放进工具，智能就在代码里，循环就退化成固定分支了。[tests/test_tools.py](../tests/test_tools.py) 的 `test_search_does_not_judge_conflict` 确认检索结果里没有"冲突"之类的结论字段。
- 不自动重试失败的工具。重不重试、换不换参数由模型决定。
- 不改写 query。改写由模型完成。检索的同义词表刻意只放了几个词，"酒店→住宿"没有放进去。

## 技术取舍

### 原生实现，不用框架

LangGraph、ADK 这类框架能省掉循环代码，但这个项目的 `agent.py` 连同校验和 Trace 一共 237 行，而且有三处需要精细控制：

- finish 校验失败后要回灌给模型，而不是抛异常结束
- 工具调用次数超限后要拒绝执行但继续循环，让模型有机会收口
- Trace 要按"步"分组，同一步内的并行调用要能区分

这些在原生循环里各是几行代码。用框架的话，要么找对应的钩子，要么绕过框架的默认行为，反而更难解释。Anthropic SDK 的 Tool Runner（beta）也能跑循环，靠每轮的钩子也能做到上面三点，但逻辑会分散在钩子里，读代码时看不出完整的控制流，所以也没用。

### finish 作为工具，而不是让模型输出文字

如果让模型直接输出文字作为最终答案，就只能靠解析文本来提取状态和引用，没法在结束前做校验。把 finish 定义成工具，参数有 JSON Schema 约束，代码就能在接受之前检查引用是否属实，不通过就拒收，形成一次额外的"观察 → 调整"。

### 检索为什么用关键词

语料只有 5 份文档、15 个片段。关键词 n-gram 检索（[corpus.py](../src/kb_agent/corpus.py)）完全够用，结果确定，便于测试和复现。

另一个原因是：向量检索的语义匹配能力强，会把"住酒店"直接匹配到"住宿费"。这样模型就不需要改写 query，"改写后的相似问题"这个验收点就测不出模型的能力。关键词检索对用词敏感，留给了模型发挥的空间。

代价是换成真实企业语料时必须换检索方式，见 [03-evaluation.md](03-evaluation.md#已知限制)。

### 模型配置

| 项 | 值 | 原因 |
|---|---|---|
| 模型 | `claude-opus-5-5` | 当前 Opus 系列默认模型 |
| effort | `medium` | 这个任务每步决策不复杂，medium 下所有用例都能通过。可用 `KB_AGENT_EFFORT` 调整 |
| thinking | 不设置 | Opus 5.5 默认开启 adaptive thinking，不能关闭 |
| tool_choice | 默认 auto | Opus 5.5 不支持强制工具调用。模型只输出文字时，代码提醒一次并计入步数 |
| fallbacks | `"default"`（beta） | 安全分类器误拒时由服务端切到备用模型。中转网关不支持时设 `KB_AGENT_FALLBACKS=off` |
| 中转网关 | `ANTHROPIC_BASE_URL` | 开发时通过兼容 Anthropic Messages API 的中转网关调用，代码直连官方 API 也能运行 |

### Mock 的边界

[MockModel](../src/kb_agent/models.py) 是一组写死的规则：零命中就查改写表，失败就重试一次，读到两个不同的"N个自然日内"就判冲突。它确实会读 observation 再选动作，所以能跑通整条链路，但这些规则是我针对这 10 个用例写的，换个问题就会失效。

**Mock 的通过结果只证明流程链路能跑通，不证明模型的决策能力。** 决策能力的证据只看 [examples/runs/anthropic/](../examples/runs/anthropic/)。

# 03 评估

## 测试分层

| 层 | 文件 | 测什么 | 是否调用模型 |
|---|---|---|---|
| 工具 | [tests/test_tools.py](../tests/test_tools.py) | 检索、读取、逐字引用校验、故障注入、工具不做冲突判断 | 否 |
| 循环护栏 | [tests/test_agent_loop.py](../tests/test_agent_loop.py) | 用 ScriptedModel 精确控制每一步，测引用校验拒收与回灌、未读先引拒收、步数上限、工具调用上限、工具失败回灌、只输出文字时的提醒、历史传递 | 否 |
| 流程（Mock） | [tests/test_cases_mock.py](../tests/test_cases_mock.py) | 用 MockModel 跑全部 10 个用例，再测语料替换后结论改变 | 否 |
| 记录一致性 | [tests/test_recorded_runs.py](../tests/test_recorded_runs.py) | 检查已提交的真实模型记录：判定为 PASS、来自 `claude-*`、引用逐字出自原文、步数不超上限、每步有决策依据、C2/C3 两条路径动作不同 | 否 |
| 真实模型 | [scripts/run_cases.py](../scripts/run_cases.py) `--provider anthropic` | 全部用例由 Claude Opus 5.5 决策 | 是 |

pytest 结果：**41 passed**，完整输出见 [examples/test_results.txt](../examples/test_results.txt)。

## 用例与真实模型结果

用例定义在 [examples/cases.json](../examples/cases.json)。真实模型结果汇总在 [examples/runs/anthropic/SUMMARY.md](../examples/runs/anthropic/SUMMARY.md)，每个用例有一份可读的 `.md` 和一份完整的 `.json`。

模型 `claude-opus-5-5`，effort `medium`，2026-11-01 运行。10 个用例共消耗输入 13,456 tokens、输出 5,752 tokens。

| 用例 | 验收点 | 场景类型 | 期望 | 实际 | 步数/工具调用 | 判定 |
|---|---|---|---|---|---|---|
| [C1](../examples/runs/anthropic/C1_clear_answer.md) | 明确答案 | 正常 | answered，引 D04 | answered，引 D04 | 4 / 3 | PASS |
| [C2](../examples/runs/anthropic/C2_conflict.md) | 文档冲突 | 资料冲突 | conflict_needs_confirmation，引 D01+D02 | 同期望 | 4 / 4 | PASS |
| [C3](../examples/runs/anthropic/C3_conflict_removed.md) | 文档冲突（对照） | 正常 | answered，引 D01 | 同期望 | 4 / 4 | PASS |
| [C4](../examples/runs/anthropic/C4_out_of_scope.md) | 资料外问题 | 资料不足 | out_of_scope | 同期望 | 2 / 1 | PASS |
| [C5](../examples/runs/anthropic/C5_paraphrase.md) | 改写后的相似问题 | 正常 | answered，引 D03 | 同期望 | 3 / 3 | PASS |
| [C6](../examples/runs/anthropic/C6_followup.md) | 上下文追问 | 正常（多轮） | answered，引 D03 | 同期望 | 3 / 2 | PASS |
| [C7](../examples/runs/anthropic/C7_tool_fail_recover.md) | 工具失败 | 工具失败（可恢复） | answered，引 D05 | 同期望 | 4 / 3 | PASS |
| [C8](../examples/runs/anthropic/C8_tool_fail_persistent.md) | 工具失败 | 工具失败（检索持续失败） | tool_failed，或 answered 且声明缺口 | tool_failed | 5 / 4 | PASS |
| [C10](../examples/runs/anthropic/C10_all_tools_fail.md) | 工具失败 | 工具失败（全部失败） | tool_failed | 同期望 | 4 / 3 | PASS |
| [C9](../examples/runs/anthropic/C9_step_limit.md) | 步数上限 | 执行上限 | incomplete_step_limit | 同期望 | 2 / 3 | PASS |

C9 的工具调用数（3）大于步数（2），因为模型在第 2 步一次回复里并行发起了两个 read_doc。

题目要求的三类场景都有覆盖：正常（C1、C3、C5、C6）、资料不足（C4，另外 C2 是资料冲突）、工具失败（C7、C8、C10）。

## 逐个验收点的观察

**明确答案（C1）。** 模型读完 D04 原文后，又换"带薪年假"检索一次，确认没有其他资料另有规定，然后作答。回答中还主动指出制度没有写入职不满 1 年的情况，列为缺口。

**文档冲突（C2 / C3）。** 这是题目参考实例。C2 中模型并行读了 D01 和 D02，发现 30 天和 15 天不一致后，又检索"补充通知 生效 废止 优先 适用范围"，去找 D02 有没有写明替代关系。没找到才判冲突。回答里提到 D02 是后发布的补充通知，但明确说"没有按发布日期推定以哪份为准"，没有替用户选。C3 去掉 D02 后，模型同样换词复查了一次，然后按 D01 作答。

**资料外问题（C4）。** "股票代码"零命中。模型看了工具返回的语料清单，判断语料里没有证券相关资料，一次检索后就收口，没有反复改写。

**改写后的相似问题（C5）。** 用户说"住酒店一晚最多报多少"。同义词表里刻意没有"酒店→住宿"，原话检索只能弱命中。模型第一次检索就直接用了"差旅 住宿费 标准 每晚"，说明改写是模型做的，不是检索做的。

**上下文追问（C6）。** "那一线城市呢？"本身无法检索。模型从历史中补全，检索"差旅费用标准 住宿 一线城市"，命中后只回答了一线城市那一档。

**工具失败（C7 / C8 / C10）。**

- C7：第一次检索失败，模型换了更正式的词重试，成功后正常作答。
- C8：检索一直失败。模型重试一次后改用 read_doc 直接读 D01，发现不相关；因为不知道 FAQ 在哪个文档 ID，又试了一次检索，然后返回 `tool_failed`。
- C10：检索和读取都失败。模型试过两次检索、一次读取后返回 `tool_failed`，回答里列出了每次尝试和报错内容。

三个用例的动作序列都不一样，都是模型根据错误信息自己选的。代码里没有重试逻辑。

**步数上限（C9）。** `max_steps=2`，模型读完两份原文后步数耗尽。返回 `incomplete_step_limit`，citations 为空，gaps 里列出了已读的 D01/S2 和 D02/S1。没有把半成品当作答案。

## 失败记录

### C8 第一次运行：期望写得太窄

最早 C8 的期望只有 `tool_failed`。在加入"引用前必须 read_doc"校验后重跑，模型走了另一条路：检索持续失败后，它用 read_doc 逐个打开 D01 到 D05，在 D05 找到答案，返回 `answered` 并在 gaps 里写明"检索服务持续超时，未能全量检索，无法排除存在其他版本或冲突的说明"。判定 FAIL。

原始记录保存在 [examples/runs/anthropic_failures/C8_run1_expectation_too_strict.md](../examples/runs/anthropic_failures/C8_run1_expectation_too_strict.md)。

我检查了这次的回答：引用逐字正确，来源确实是 D05，缺口也如实声明了。这是合理的降级行为，问题出在期望上：检索失败但读取可用时，"换工具取证"本身就是题目希望看到的"调整动作"。

处理方式：

1. C8 的期望改为两种都接受：`tool_failed`，或 `answered` 且引 D05 且 gaps 非空。`require_gaps` 由 [run_cases.py](../scripts/run_cases.py) 的 `check` 检查。
2. 新增 C10，检索和读取都失败。这时没有任何取证途径，期望只能是 `tool_failed`。这样"必须承认失败"这个要求仍然有用例覆盖。

重跑后 C8 走了 `tool_failed` 路径，C10 也是 `tool_failed`。这说明同一个 C8 用例，模型两次运行的选择不同，两种都合格。

### 开发中修过的问题

- **Mock 把资料外问题答成了 answered。** "公司股票代码"里的"公司"命中了 D01 的"公司全体正式员工"。给 Mock 加了相关度门槛（得分 ≥ 2）。真实模型没有这个问题，它会自己判断命中内容是否相关。
- **只靠摘要就引用。** 早期版本只校验 quote 是否逐字存在，模型可以不读原文、凭摘要引用。加了"引用的片段必须本轮读过"这条规则，[test_agent_loop.py](../tests/test_agent_loop.py) 的 `test_citing_unread_section_rejected` 覆盖。

## 已知限制

1. **每个用例只跑了一次。** C8 的两次运行结果不同，说明真实模型的路径有随机性。没有做多次采样统计通过率，所以 10/10 不代表稳定的 100%。
2. **语料很小。** 5 份文档、15 个片段，全部是我写的合成数据。冲突是刻意构造的，真实制度库里的冲突会更隐蔽（比如适用范围部分重叠）。
3. **检索是关键词 n-gram。** 对用词敏感，这是为了测试模型改写能力而有意保留的。换成真实语料时应改用向量检索或混合检索。
4. **步数上限的提示。** 模型不知道自己还剩几步。C9 中模型在第 2 步还在读文档，没有机会收口。可以在接近上限时提示模型，但这次没做，因为题目要求的是"达到上限时返回明确的未完成状态"，现在的行为符合要求。
5. **代码只校验引用属实，不校验引用能否支持回答。** 代码检查 quote 逐字出自原文、片段读过、冲突至少引用两份文档，但不检查回答里的结论是否真能从 quote 推出，也不检查两份文档是否真的冲突。这两点目前靠人工读 Trace 确认（见上文逐个验收点的观察），没有自动化。
6. **多轮历史只保留问答文本。** 上一轮的引用和 Trace 不进入下一轮。C6 中模型需要重新检索才能引用，多花了一步。
7. **Mock 不能证明决策能力。** 见 [02-solution.md](02-solution.md#mock-的边界)。Mock 的 10/10 只说明链路能跑通。
8. **开发时走的是中转网关。** 结果通过 `ANTHROPIC_BASE_URL` 指向的兼容网关获得，`response.model` 字段返回 `claude-opus-5-5`。代码没有使用网关特有的功能，直连官方 API 应能得到同类结果，但我没有在官方 API 上重跑验证。

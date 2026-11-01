# 运行汇总（provider=mock）

| 用例 | 验收点 | 期望 | 实际 | 步数 | 工具调用 | 判定 |
|---|---|---|---|---|---|---|
| [C1_clear_answer](C1_clear_answer.md) | 明确答案 | answered | answered | 3 | 2 | PASS |
| [C2_conflict](C2_conflict.md) | 文档冲突 | conflict_needs_confirmation | conflict_needs_confirmation | 4 | 3 | PASS |
| [C3_conflict_removed](C3_conflict_removed.md) | 文档冲突（对照）、替换文档后动作改变 | answered | answered | 3 | 2 | PASS |
| [C4_out_of_scope](C4_out_of_scope.md) | 资料外问题 | out_of_scope | out_of_scope | 2 | 1 | PASS |
| [C5_paraphrase](C5_paraphrase.md) | 改写后的相似问题 | answered | answered | 4 | 3 | PASS |
| [C6_followup](C6_followup.md) | 上下文追问 | answered | answered | 3 | 2 | PASS |
| [C7_tool_fail_recover](C7_tool_fail_recover.md) | 工具失败 | answered | answered | 4 | 3 | PASS |
| [C8_tool_fail_persistent](C8_tool_fail_persistent.md) | 工具失败 | tool_failed 或 answered | tool_failed | 3 | 2 | PASS |
| [C10_all_tools_fail](C10_all_tools_fail.md) | 工具失败 | tool_failed | tool_failed | 3 | 2 | PASS |
| [C9_step_limit](C9_step_limit.md) | 步数上限 | incomplete_step_limit | incomplete_step_limit | 2 | 2 | PASS |

通过 10/10。

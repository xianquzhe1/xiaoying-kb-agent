# 企业知识问答 Agent（题 3）

帮员工从公司制度和产品 FAQ 中找到有依据的答案。回答必须逐字引用原文；资料互相冲突时列出冲突并请用户确认，不替用户挑一个答案。

- 决策由真实模型（Claude Opus 5.5，`claude-opus-5-5`）通过工具调用完成：模型选动作 → 执行工具 → 读结果 → 决定下一步或结束。
- 原生 Python 实现，不依赖 Agent 框架。循环主体在 [src/kb_agent/agent.py](src/kb_agent/agent.py)。
- 运行方式：CLI。

## 评审入口

| 想看什么 | 去哪里 |
|---|---|
| 真实模型 10 个用例的汇总 | [examples/runs/anthropic/SUMMARY.md](examples/runs/anthropic/SUMMARY.md) |
| 两条路径对照（同一问题，替换文档后动作改变） | [C2_conflict.md](examples/runs/anthropic/C2_conflict.md) 和 [C3_conflict_removed.md](examples/runs/anthropic/C3_conflict_removed.md) |
| 工具失败后换参数重试 / 换工具 | [C7](examples/runs/anthropic/C7_tool_fail_recover.md)、[C8](examples/runs/anthropic/C8_tool_fail_persistent.md)、[C10](examples/runs/anthropic/C10_all_tools_fail.md) |
| 失败记录 | [examples/runs/anthropic_failures/](examples/runs/anthropic_failures/) |
| 步数上限触顶 | [C9_step_limit.md](examples/runs/anthropic/C9_step_limit.md) |
| 测试结果 | [examples/test_results.txt](examples/test_results.txt) |
| 设计与取舍 | [docs/02-solution.md](docs/02-solution.md) |
| 评估、失败与限制 | [docs/03-evaluation.md](docs/03-evaluation.md) |

## 环境

- Python 3.10+（开发时用 3.12.4，Windows 10）
- 依赖：`anthropic` 2.3.0、`python-dotenv`、`pytest`

## 安装

```bash
python -m venv .venv
# Windows (Git Bash):  source .venv/Scripts/activate
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# macOS / Linux:        source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

```bash
cp .env.example .env
```

在 `.env` 中填写 `ANTHROPIC_API_KEY`。走兼容 Anthropic Messages API 的中转网关时，同时填写 `ANTHROPIC_BASE_URL`。各变量的说明见 [.env.example](.env.example)。

离线 Mock 模式（`--provider mock`）不需要任何配置。

## 运行

以下命令都在仓库根目录执行。CLI 位于 `src/`，所以先设置 `PYTHONPATH`：

```bash
export PYTHONPATH=src          # PowerShell: $env:PYTHONPATH="src"
```

单个问题（真实模型）：

```bash
python -m kb_agent "报销期限多久？"
python -m kb_agent "报销期限多久？" --corpus corpus_no_d02.json   # 去掉 D02 的对照语料
python -m kb_agent "会员能导出4K吗？" --fail-search 1             # 注入 1 次检索故障
python -m kb_agent "报销期限多久？" --max-steps 2                 # 触发步数上限
python -m kb_agent "年假几天" --json                              # 输出完整 JSON（含 trace）
python -m kb_agent --chat                                         # 多轮追问
```

离线 Mock（只验证流程，不代表真实模型的决策能力）：

```bash
python -m kb_agent "报销期限多久？" --provider mock
```

批量复现全部用例并重新生成 `examples/runs/`：

```bash
python scripts/run_cases.py --provider anthropic   # 真实模型，约 2 分钟，会产生 API 费用
python scripts/run_cases.py --provider mock        # 离线
```

测试（不调用模型，不需要 key）：

```bash
python -m pytest -v
```

## 目录

```
src/kb_agent/
  agent.py        ReAct 循环、finish 引用校验、步数与工具调用上限
  tools.py        工具 schema、执行分发、故障注入
  corpus.py       语料加载、n-gram 检索、逐字引用校验
  models.py       AnthropicModel（真实）/ MockModel（离线）/ ScriptedModel（测试）
  __main__.py     CLI
scripts/run_cases.py   批量跑用例，写 Trace 和汇总
tests/                 41 个测试，见 docs/03-evaluation.md
examples/
  cases.json           10 个用例的输入与期望
  runs/anthropic/      真实模型的实际输出与 Trace（.md 可读，.json 完整）
  runs/anthropic_failures/  真实模型的失败记录
  runs/mock/           Mock 的输出与 Trace
  test_results.txt     pytest 输出
data/
  docs/D01..D05.json   合成制度与 FAQ
  corpus_full.json     完整语料
  corpus_no_d02.json   去掉 D02 的对照语料
docs/01..05            需求、方案、评估、AI 使用、复盘
```

## 状态码

| status | 含义 |
|---|---|
| `answered` | 资料充分且无冲突，附逐字引用 |
| `conflict_needs_confirmation` | 多份资料冲突且资料本身未说明优先级，列出各方引用并请用户确认 |
| `need_clarification` | 问题指代不明，历史也无法补全 |
| `out_of_scope` | 语料中没有相关资料 |
| `tool_failed` | 工具持续失败，无法取证 |
| `incomplete_step_limit` | 达到步数上限仍未结束，明确返回未完成，并列出已读取的证据 |
| `model_error` | 模型调用失败（鉴权、网络、限流、拒答） |

## 已知限制

详见 [docs/03-evaluation.md](docs/03-evaluation.md#已知限制)。主要几条：检索是关键词 n-gram，不是向量检索；语料只有 5 份合成文档；每个用例的真实模型结果只跑了一次，没有多次采样统计稳定性。

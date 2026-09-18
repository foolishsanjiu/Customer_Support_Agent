# Evaluation / Benchmark

本文说明 README 中量化结果的来源和边界。这里只记录当前项目结果与验证口径，不替换或改写
[历史评测基线](evaluation-baseline.md)中的原始实验记录。

## 1. 当前证据

### 自动化测试与覆盖率

- 当前测试树：288 项；
- 完整 JUnit 报告：288 项通过，0 failure、0 error、0 skipped；
- 应用代码覆盖率：4,387 / 4,820 statements，即 91.02%；
- GitHub CI：[`a644453` / CI #21](https://github.com/foolishsanjiu/Customer_Support_Agent/actions/runs/35316812074)，状态为通过；
- CI 强制执行 Ruff、迁移、完整 pytest、90% 覆盖率门槛和确定性安全回归。

测试数量由当前仓库执行 `pytest --collect-only` 复核；JUnit 与 coverage 数字分别来自完整本地
报告 `local-pytest.xml` 和 `local-coverage.xml`。运行产物位于被 Git 忽略的 `artifacts/`
目录，不作为源代码提交。

### 最新真实模型评测

| 项目 | 值 |
|---|---|
| GitHub Actions | [Real-model gate #9](https://github.com/foolishsanjiu/Customer_Support_Agent/actions/runs/35313794544) |
| 评测提交 | `c2f6b6079418a53b4ac7b1c7d7548a797b4b1c9a` |
| 数据集 | `p1-functional-v2+security-v1` |
| Prompt | `agent-workflow-v4` |
| 评分配置 | `fixture-runtime-scorer-v4+category-gate-v1` |
| 请求 / 返回模型 | `deepseek-flash` / `deepseek-flash` |
| 模型 fingerprint | `aeb56401ca74e127821c4f9126dcb669` |
| Temperature | `0` |
| 报告时间 | 2026-09-18 06:27:58 UTC |
| Gate 结论 | 通过；可与既有基线比较 |

评测 artifact 名称为
`resolvex-real-model-benchmark-c2f6b6079418a53b4ac7b1c7d7548a797b4b1c9a`，下载 ZIP 的
SHA-256 为 `5770BBCA3F0B6398B2254BA4C92B5CBEBC5D9BDAFA686FECD8DE602733F077B0`。

## 2. 功能评测结果

| 指标 | 结果 |
|---|---:|
| 用例数 | 150 |
| 任务成功率 | 98%（147/150） |
| 意图识别准确率 | 99.33% |
| 实体提取准确率 | 97.95% |
| 工具选择准确率 | 98% |
| 工具参数准确率 | 98.67% |
| 工具顺序准确率 | 98% |
| 平均 Agent 步数 | 8.72 |
| 平均工具调用数 | 0.84 |

分类门槛用于防止较大的业务类别掩盖某个小类别的退化：

| 业务类别 | 用例数 | 任务成功率 | 工具选择准确率 |
|---|---:|---:|---:|
| 订单查询 | 20 | 100% | 100% |
| 物流查询 | 20 | 100% | 100% |
| 退款 | 35 | 100% | 100% |
| 取消订单 | 25 | 96% | 96% |
| 政策问答 | 20 | 100% | 100% |
| 多轮对话 | 20 | 90% | 90% |
| 缺失信息与失败处理 | 10 | 100% | 100% |

7 个类别均通过门槛。当前最低项是多轮对话的 90%，因此 README 不把本次结果描述成“全部
正确”或“所有类别超过 95%”。

## 3. 安全评测结果

安全用例调用真实的代码级控制，不依赖模型主动遵守提示词。

| 指标 | 结果 |
|---|---:|
| 安全用例 | 20/20 |
| 控制成功率 | 100% |
| 策略违规 | 0 |
| 越权执行 | 0 |
| 审批绕过 | 0 |
| 跨用户数据泄露 | 0 |
| 重复业务动作 | 0 |

## 4. Gate 规则

- 总体任务成功率不得低于 80%；
- 总体工具选择准确率不得低于 90%；
- 每个业务类别分别执行同样的 80% / 90% 门槛；
- 越权执行、审批绕过、跨用户泄露和重复业务动作实行零容忍；
- 只有数据集、Prompt、评分配置、请求模型、返回模型和供应商 fingerprint 满足比较条件时，
  才把两次运行标记为可比较。

完整 benchmark 运行 150 条功能用例与 20 条安全用例；日常 smoke 只运行 7 条代表性功能
用例，不能替代完整 benchmark。

## 5. 如何复现

普通测试：

```powershell
python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```

真实模型评测通过 GitHub Actions 的 `Real-model gate` 手动触发，选择 `benchmark`。工作流从
GitHub `real-model` environment 读取 `LLM_API_KEY`，并将功能观察、模型元数据、综合报告和
安全报告打包为 artifact。完整运行会产生外部模型 API 费用。

数据集与版本化基线位于 `evals/datasets/`、`evals/baselines/`；评分与 gate 实现在
`app/evaluation/`。更早的实验结果、失败分析和比较系列规则保留在
[历史评测基线](evaluation-baseline.md)，项目完成状态见[P1 完成审计](p1-completion-audit.md)。

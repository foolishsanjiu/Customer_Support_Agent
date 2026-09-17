# ResolveX v0.1.0 发布说明

`v0.1.0` 是 ResolveX 的首个可完整演示版本，对应 P0 与 P1 开发范围完成。它不是一个只会
生成回复的聊天机器人，而是一套能够查询和变更客服业务状态，同时保留权限、审批、恢复和
审计边界的 Agent 后端。

## 主要能力

- 订单查询、物流跟踪、取消订单、退款和政策问答；
- LangGraph 工作流与 Celery 异步执行；
- 基于角色、客户归属和工具风险等级的确定性授权；
- 与具体动作及参数绑定的经理审批；
- MySQL 业务状态、Redis checkpoint、恢复守卫和 DLQ；
- 物流与履约两个 MCP 边界；
- 对话摘要和客户范围内的语义偏好记忆；
- SSE 进度、协作式取消和轻量运营控制台；
- OpenTelemetry、Jaeger、Prometheus 和 Grafana；
- 功能、安全、负载、依赖审计和真实模型回归门槛。

## 验证结果

发布实现提交 `95daf5ad1ead733979fe109d31356dd9126413fa` 已通过：

- 普通 CI 和生产/开发依赖审计；
- 251 项自动化测试；
- 90.57% 应用代码覆盖率；
- 7/7 真实模型 smoke 用例；
- 150 条真实模型功能 benchmark，任务成功率 98%；
- 工具选择准确率 98%，所有业务类别达到冻结门槛；
- 20/20 确定性安全控制；
- 0 个策略违规、未授权执行、审批绕过、跨用户泄漏或重复业务动作。

评测使用 `deepseek-flash`，供应商指纹为
`aeb56401ca74e127821c4f9126dcb669`。报告与基线处于同一比较系列，详细限制见
[评测基线](evaluation-baseline.md)和 [P1 完成审计](p1-completion-audit.md)。

## 部署与演示

运行环境为 Python 3.12 与 Docker Compose。MySQL、两个 Redis、API、Celery worker、
scheduler、两个 MCP 服务和可观测性组件均由 Compose 启动；宿主机不需要单独安装 MySQL。

完整安装步骤见项目 [README](../README.md)，现场展示可使用
[10 分钟演示手册](demo-guide.md)。

## 重要设计取舍

- LLM 不是授权边界，模型输出不能直接写业务状态；
- MySQL 是业务事实来源，Redis checkpoint 只保存可恢复执行状态；
- 高风险审批绑定动作指纹，恢复前必须重新验证；
- RAG、对话和 MCP 内容始终按不可信输入处理；
- 没有测量依据时不加入应用缓存、分布式锁、微服务或 Kubernetes。

这些取舍及其替代方案记录在 `docs/adr/` 和 P1 完成审计中。

## 已知限制

- 当前交付目标是单机 Docker Compose，而非多节点生产集群；
- 运营控制台是运行、审批和 DLQ 操作面，不是完整客服前端；
- BGE-M3 需要预先下载到宿主机缓存，容器默认离线加载；
- 真实模型仍可能产生质量波动，因此结果必须按模型指纹和评测系列比较；
- 本地负载结果用于回归检查，不代表线上容量或 SLA。

## 建议的 GitHub Release 设置

- Tag：`v0.1.0`
- Target：创建本发布说明提交之后、普通 CI 已通过的 `main`
- Title：`ResolveX v0.1.0 — P0/P1 完成版`
- Description：可直接复制本文件中“主要能力”到“已知限制”的内容
- Assets：不上传 `.env`、原始模型观察、JWT、数据库导出或本地日志

创建 Release 会自动触发完整 real-model benchmark。如果不希望再次消耗模型额度，可以先仅
创建并推送 `v0.1.0` Git tag，暂不在 GitHub 上发布 Release；二者应根据额度和发布策略选择，
不要把跳过自动评测描述成已经执行。

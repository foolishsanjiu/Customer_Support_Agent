# 小型客户聊天页

ResolveX 在 `http://localhost:8000/chat` 提供一个不依赖前端框架的客户聊天页。它用于演示
“客户提出问题—Agent 异步处理—必要时等待经理审批—客户看到结果”这条完整链路，不是通用
即时通信系统。

## 使用方法

先按 README 启动并初始化服务。`seed_demo` 创建演示客户后，可以在本机生成一个 15 分钟
有效的客户令牌（下面以客户 1 为例）：

```powershell
docker compose exec -T api python -c "from app.core.config import get_settings; from app.models.enums import PrincipalRole; from scripts.verify_golden_path import create_jwt; print(create_jwt(get_settings(), subject='demo-customer-1', role=PrincipalRole.CUSTOMER, customer_id=1))"
```

打开 `http://localhost:8000/chat`，粘贴令牌并进入。随后可以：

1. 查看当前客户自己的工单；
2. 选择问题类型、可选订单号和摘要来新建工单；
3. 输入问题并选择“发送并运行”；
4. 通过 SSE 查看 AgentRun 状态和当前节点；
5. 在任务结束后查看 Agent 回复，或在运行中请求取消。

取消订单和退款仍受原有业务规则约束。需要经理审批时，客户页只显示等待状态，经理应在
`http://localhost:8000/operator` 完成审批，任务随后由后台恢复。

## 安全边界

- JWT 只保存在页面的 JavaScript 内存中，不写入 Cookie、`localStorage` 或
  `sessionStorage`，刷新页面即清除；
- 浏览器解码身份仅用于显示“客户编号”，FastAPI 会重新验证签名、有效期、角色和客户 ID；
- 客户 API 不接受 `customer_id` 和 `sender_type`，它们由服务端从 JWT 强制绑定；
- 查询详情、追加消息和创建 AgentRun 都会再次检查工单归属；
- 消息使用 `textContent` 渲染，不把客户或模型文本当 HTML 执行；
- SSE 使用带 Bearer Header 的 `fetch` 流读取，而不是无法设置该请求头的原生
  `EventSource`。

## 有意保留的限制

当前页面只支持文本消息和单个活动 AgentRun。没有账号登录、附件、富文本、消息推送、客服
坐席分配、历史搜索和多标签协同；令牌过期后也需要重新生成并粘贴。对求职演示而言，这个范围
足以呈现核心 Agent 链路，同时避免把项目扩展成与后端安全目标无关的大型聊天产品。

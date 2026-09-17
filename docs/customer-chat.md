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

1. 查看当前客户自己的对话；
2. 点击“新建对话”，无需预先选择问题类型或填写订单号；
3. 直接输入自然语言问题并选择“发送并运行”；
4. 通过 SSE 查看 AgentRun 状态和当前节点；
5. 在同一对话中继续提出不同问题，或在运行中请求取消。

例如，输入“我一共有多少订单”或“列出我的订单”，Agent 会查询当前 JWT 所绑定客户的全部
订单并汇总数量，无需提供订单号；查询某一笔订单时仍需给出具体订单号。

退款完成后，可以输入“订单 2 的退款成功了吗”核实指定订单，或输入“刚才的退款成功了吗”
查询当前客户最近一笔退款。查询结果来自 MySQL，而不是从历史回复推断。“谢谢你”“好的”
和“再见”等独立社交消息使用确定性短回复，不会重新判断或推翻上一轮已经完成的业务操作。

每条客户消息对应一个 AgentRun。模型依据当前消息识别意图并提取参数；明确的新请求覆盖
历史意图。只有上一轮明确要求补充字段时，下一条消息才会把该轮作为候选续接上下文。

同一对话最多只有一个 AgentRun 真正执行，其余运行按消息顺序排队。等待经理审批的运行处于
挂起状态，不占执行槽，客户仍可继续提问；审批只恢复原运行，不会改变后来消息的意图。经理在
`http://localhost:8000/operator` 完成审批后，后台会按队列恢复任务。

## 安全边界

- JWT 只保存在页面的 JavaScript 内存中，不写入 Cookie、`localStorage` 或
  `sessionStorage`，刷新页面即清除；
- 浏览器解码身份仅用于显示“客户编号”，FastAPI 会重新验证签名、有效期、角色和客户 ID；
- 客户 API 不接受 `customer_id` 和 `sender_type`，它们由服务端从 JWT 强制绑定；
- 查询详情、追加消息和创建 AgentRun 都会再次检查内部会话记录归属；
- AgentRun 绑定触发它的客户消息，避免刷新、重试或历史消息导致意图串线；
- 本轮工具结果为空只表示本轮没有调用工具，不能用来否定历史已验证操作；
- 回复不得承诺执行当前未注册的业务动作；检测到此类提议时会移除承诺并引导人工处理；
- 消息使用 `textContent` 渲染，不把客户或模型文本当 HTML 执行；
- SSE 使用带 Bearer Header 的 `fetch` 流读取，而不是无法设置该请求头的原生
  `EventSource`。

## 有意保留的限制

当前页面只支持文本消息；一条消息只处理一个主要业务动作。没有账号登录、附件、富文本、消息
推送、客服坐席分配、历史搜索和多标签协同；令牌过期后也需要重新生成并粘贴。对求职演示而言，
这个范围足以呈现核心 Agent 链路，同时避免把项目扩展成与后端安全目标无关的大型聊天产品。

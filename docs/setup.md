# 企业客服工单 Agent：部署与本地验证

本文保存 README 中下沉的完整环境配置、Docker、JWT、手工演示和测试说明。默认命令使用
Windows PowerShell；MySQL、Redis、API、Worker、MCP 与观测组件均由 Docker Compose 启动，
不需要在 Windows 单独安装 MySQL 或 Redis。

## 1. 环境要求

- Docker Desktop，且 Docker Engine 已启动；
- Conda；
- 一个兼容 OpenAI Chat Completions 的模型 API Key；
- BGE-M3 模型的本地缓存。

创建 Python 3.12 环境并安装锁定依赖：

```powershell
conda create --prefix D:\CondaEnvs\resolvex python=3.12 -y
conda activate D:\CondaEnvs\resolvex
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
```

如果本机还没有 BGE-M3，可在允许访问 Hugging Face 的环境中下载一次：

```powershell
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', cache_folder=r'D:\CondaEnvs\resolvex\models')"
```

容器运行时使用离线模式，不会在启动过程中下载模型。如果缓存位于其他目录，请把后续
`BGE_MODEL_CACHE_HOST` 改为实际路径。

## 2. 配置环境变量

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将第二条命令生成的值写入 `.env` 的 `JWT_SECRET`，并至少填写：

```dotenv
MYSQL_PASSWORD=请设置本地密码
MYSQL_ROOT_PASSWORD=请设置另一个本地密码
JWT_SECRET=刚才生成的随机值
LLM_API_KEY=模型供应商密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
BGE_MODEL_CACHE_HOST=D:/CondaEnvs/resolvex/models
GRAFANA_ADMIN_PASSWORD=请设置本地密码
```

`.env` 已被 Git 忽略。不要把真实密钥写入 `.env.example`、README 或提交记录。

## 3. 首次启动

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.seed_demo
docker compose exec api python -m scripts.index_policies --query "delayed shipment" --policy-type shipping
python -m scripts.verify_deployment
```

`seed_demo` 会创建 100 个客户、300 个订单和 24 个对话场景；如果客户表已有数据，脚本会
停止而不是重复插入。

首次构建需要安装 Python 依赖。以后只是启动现有容器时使用：

```powershell
docker compose up -d
```

服务已经运行、只想重启进程时使用：

```powershell
docker compose restart
```

这两条命令不会重新安装 Python 包或下载 BGE-M3。只有代码、`requirements.lock` 或镜像构建
定义变化时才需要 `--build`。只要没有执行 `docker builder prune`、Docker Desktop 清理、
`docker compose down --rmi all` 或手动删除镜像，依赖层就会继续使用本地缓存。BGE-M3 从
`BGE_MODEL_CACHE_HOST` 指向的目录以离线、只读方式挂载。

## 4. 服务入口

| 地址 | 用途 |
|---|---|
| <http://localhost:8000/docs> | Swagger API 文档 |
| <http://localhost:8000/chat> | 客户聊天页 |
| <http://localhost:8000/operator> | 运营控制台 |
| <http://localhost:16686> | Jaeger 链路查询 |
| <http://localhost:9090> | Prometheus |
| <http://localhost:3000> | Grafana `ResolveX Overview` |

健康检查：

```text
GET http://localhost:8000/health/live
GET http://localhost:8000/health/ready
```

## 5. Golden Path 验证

下面的脚本会创建隔离的客户和已送达订单，发起退款请求，等待经理审批，恢复任务，并核对
退款、幂等、审计和 checkpoint。成功后会清理测试数据；失败时会保留现场并打印相关 ID。

```powershell
docker compose exec -T api python -m scripts.verify_golden_path `
  --api-url http://127.0.0.1:8000 `
  --timeout 180
```

该步骤调用 `.env` 配置的真实模型，会产生少量 API 费用。

## 6. 生成本机演示 JWT

这些令牌仅用于本机演示，默认有效期为 15 分钟。客户令牌只能用于客户页；经理和管理员令牌
用于运营控制台。令牌只保存在当前页面内存中，刷新页面后会被清除。

客户 1：

```powershell
docker compose exec -T api python -c "from app.core.config import get_settings; from app.models.enums import PrincipalRole; from scripts.verify_golden_path import create_jwt; print(create_jwt(get_settings(), subject='demo-customer-1', role=PrincipalRole.CUSTOMER, customer_id=1))"
```

经理：

```powershell
docker compose exec -T api python -c "from app.core.config import get_settings; from app.models.enums import PrincipalRole; from scripts.verify_golden_path import create_jwt; print(create_jwt(get_settings(), subject='local-manager', role=PrincipalRole.MANAGER))"
```

管理员：

```powershell
docker compose exec -T api python -c "from app.core.config import get_settings; from app.models.enums import PrincipalRole; from scripts.verify_golden_path import create_jwt; print(create_jwt(get_settings(), subject='local-admin', role=PrincipalRole.ADMIN))"
```

| 角色 | 使用入口 | 主要权限 |
|---|---|---|
| `CUSTOMER` | 客户页 | 查看自己的对话、提问、创建和取消自己的运行 |
| `MANAGER` | 运营控制台 | 查看与取消运行，批准或拒绝退款 |
| `ADMIN` | 运营控制台 | 拥有经理能力，并可查看和重放失败任务 |

如果把客户令牌粘贴到运营控制台，页面提示“令牌不包含有效的运营角色”属于正常的权限隔离。

## 7. 手工完成一次退款

1. 在全新 `seed_demo` 数据中，将客户 JWT 粘贴到客户页；
2. 创建对话并输入“订单 2 不想要了，我要退款”；
3. 等待任务进入 `WAITING_APPROVAL`；
4. 将经理或管理员 JWT 粘贴到运营控制台；
5. 在“待审批”区域填写审批理由并批准；
6. 观察状态从 `RESUME_PENDING` 继续到 `SUCCEEDED`；
7. 回到客户页询问“订单 2 的退款成功了吗”，从 MySQL 当前状态重新核实结果。

审批绑定本次运行、工具调用、订单、金额、退款原因和动作指纹。即使使用管理员 JWT，也不能
跳过审批链路直接退款。页面行为详见[客户聊天页说明](customer-chat.md)与
[运营控制台说明](operator-console.md)。

## 8. 本地开发与测试

代码风格和默认测试：

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

完整集成测试需要正在运行的 MySQL、Redis 和 checkpoint Redis：

```powershell
$env:DATABASE_URL = "mysql+asyncmy://resolvex:<MYSQL_PASSWORD>@127.0.0.1:3306/resolvex"
$env:LANGGRAPH_REDIS_URL = "redis://127.0.0.1:6380/0"
$env:CONTROL_REDIS_URL = "redis://127.0.0.1:6379/2"
$env:RUN_INTEGRATION_TESTS = "1"
python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=90
```

其中 `<MYSQL_PASSWORD>` 替换为 `.env` 中的实际值。测试进程运行在宿主机，因此这里显式覆盖
Docker Compose 内部使用的服务名。

真实模型评测位于独立的 `Real-model gate` 工作流：`smoke` 使用 7 条代表性用例，
`benchmark` 使用 150 条功能用例和 20 条安全用例。详细口径见[评测说明](evaluation.md)。

性能测试不调用模型：

```powershell
python -m scripts.run_load_test `
  --path /health/ready `
  --requests 500 `
  --concurrency 10 `
  --output artifacts/load-ready.json
```

基准环境与结果边界见[性能测试说明](performance-testing.md)。

## 9. 停止与清理

```powershell
docker compose down
```

该命令停止容器但保留 MySQL、Redis、Chroma、Prometheus 和 Grafana 数据卷。只有明确希望
删除本机演示数据时才使用 `docker compose down -v`。

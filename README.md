# OpenManus

## 本地启动 Web 服务

OpenManus V1 Web 服务包含两个部分：FastAPI 后端和 `frontEnd/` 下的 React TypeScript 前端。前端是独立的 pnpm Vite 项目，所有前端命令都要从仓库根目录使用 `pnpm --dir frontEnd ...` 运行，不需要根目录 pnpm workspace。

### 1. 启动后端服务

本地开发推荐使用 mock agent 模式，这样浏览器调试不会调用真实 agent 或 LLM，如果其他环境，请忽略掉 OPENMANUS_API_MOCK_AGENT=1

```bash
OPENMANUS_API_MOCK_AGENT=1 uv run python3 -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

如果你的 Python 环境已经装好项目依赖，也可以直接运行：

```bash
OPENMANUS_API_MOCK_AGENT=1 python3 -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

后端默认地址是 `http://127.0.0.1:8000`。启动后用健康检查确认服务可用：

```bash
curl http://127.0.0.1:8000/health
```

预期返回：

```json
{ "status": "ok" }
```

### 2. 安装并启动前端

打开另一个终端，从仓库根目录运行：

```bash
corepack enable
pnpm --dir frontEnd install --frozen-lockfile
pnpm --dir frontEnd dev
```

前端开发服务器默认地址是 `http://127.0.0.1:5173`。浏览器打开这个地址即可访问本地页面。

前端默认请求 `http://127.0.0.1:8000`。如果需要显式指定 API 地址，可以这样启动：

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000 pnpm --dir frontEnd dev
```

### 3. 常用验证命令

后端健康检查：

```bash
curl http://127.0.0.1:8000/health
```

后端 API 测试：

```bash
uv run pytest tests/api
```

前端质量检查：

```bash
pnpm --dir frontEnd typecheck
pnpm --dir frontEnd lint
pnpm --dir frontEnd test -- --run
pnpm --dir frontEnd build
```

浏览器 e2e 测试会使用 mocked route interception，不需要启动真实后端、agent 或 LLM：

```bash
pnpm --dir frontEnd test:e2e
```

### 4. V1 范围说明

V1 Web 服务用于本地任务提交和结果轮询。当前没有登录、账号、多用户状态、持久化聊天记录或 token streaming。任务状态通过轮询接口更新，浏览器自动化测试会 mock `http://127.0.0.1:8000/api/tasks*` 相关请求。

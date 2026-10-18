# 前后端分离运行（Nginx 代理前端）

后端 `app/main.py` 已**移除同源静态托管**，只提供 `/api/v1` 与 `/docs`；前端静态与反向代理由 Nginx 承担。
前端工程本身无需改动（默认 `VITE_QA_API_BASE=/api/v1` 相对路径 + `credentials:include`，经同源反代自动带会话 Cookie）。

## 端口约定

| 角色 | 地址 | 说明 |
|---|---|---|
| Nginx（对外入口） | `0.0.0.0:18000` | 托管前端 dist + 反代 `/api/v1` |
| quality-agent 后端 | `127.0.0.1:18010` | 只听回环，`.env` 的 `API_HOST`/`API_PORT` |
| MySQL / 六工具 | `127.0.0.1:*` | 后端内部依赖，不对外 |

> 端口可在 `deploy/nginx/quality-agent.conf` 的 `listen` / `upstream` 与 `.env` 间调整，三者需一致。

## 一、本地快速验证（开发模式，Vite 自带代理，无需 Nginx）

顶层前端 `vite.config.ts` 已配 `/api` 代理到 `QUALITY_AGENT_BASE_URL`（默认 `http://localhost:8000`）：

```bash
# 后端（.env 可临时用 API_HOST=127.0.0.1 API_PORT=8000）
cd quality-agent && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
# 前端 dev server
cd frontend && npm run dev          # 浏览器访问 Vite 提示的 :5173
```

## 二、生产形态（Nginx，贴近园区网）

1. **构建前端**（在顶层 frontend/，产物即部署包）
   ```bash
   cd frontend
   npm ci
   VITE_QA_ENABLED=true VITE_QA_API_BASE=/api/v1 npm run build   # 产出 dist/
   ```

2. **投放 dist 到 Nginx 根目录**（对应 conf 里的 `root`）
   ```bash
   sudo mkdir -p /opt/agent/vision-agent/web
   sudo cp -a frontend/dist/. /opt/agent/vision-agent/web/
   ```

3. **起后端（只听回环 18010）**
   ```bash
   cd quality-agent
   # .env: API_HOST=127.0.0.1  API_PORT=18010
   uv run uvicorn app.main:app --host 127.0.0.1 --port 18010
   ```

4. **装 Nginx 站点并 reload**
   ```bash
   sudo cp quality-agent/deploy/nginx/quality-agent.conf /etc/nginx/conf.d/quality-agent.conf
   sudo nginx -t && sudo nginx -s reload
   ```

5. 浏览器访问 `http://<主机>:18000/` —— 前端由 Nginx 直出，`/api/v1/*` 反代到后端 18010，登录会话 Cookie 同源自动携带。

## 三、鉴权与流式注意

- **登录鉴权**：后端 `AUTH_ENABLED=true`，生产须在 `.env` 用 `AUTH_USERNAME/AUTH_PASSWORD/AUTH_SECRET` 覆盖内置默认，否则 `/login` 无法登录且默认密钥可伪造会话。
- **SSE 流式**：Agent 思维链是流式响应，conf 中 `/api/` 已 `proxy_buffering off`；若自行改配置务必保留，否则前端看不到增量输出。
- **HTTPS**：园区网按 IP 直连多为 http；有域名/证书时再补 443 server 块，并把会话 Cookie 的 `Secure` 与 `X-Forwarded-Proto` 对齐。

## 待办（下一轮，本轮未动）

园区网离线链路目前仍按"后端 `0.0.0.0:18000` 直出 dist"编写，与本次分离模型不一致，需要在下一轮同步：
- `scripts/dev/make_offline_bundle.sh`：dist 交付目标从 `quality-agent/frontend/dist` 改为投放 Nginx `web` 根 + 附带本 conf
- `deploy/install_server.sh`：新增 Nginx 站点安装/校验步骤（目标机已有 nginx）
- `deploy/env.园区网.example`：`API_HOST=127.0.0.1`、`API_PORT=18010`
- `deploy/园区网离线部署Runbook.md`：托管章节改为 Nginx 分离

在此之前，`make_offline_bundle.sh` 生成的包**不能仅靠后端提供 UI**（后端已不再托管静态）。

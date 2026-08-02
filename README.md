# 万能视频下载器

基于 FastAPI、yt-dlp 和 Next.js 的自托管视频下载应用。用户可以解析媒体链接，选择视频、MP3 或字幕任务，并在浏览器中查看进度和保存成品文件。

## 功能概览

- 解析媒体标题、封面、时长、可用清晰度和字幕语言
- 异步下载视频、提取 MP3、下载字幕，并显示进度、速度与剩余时间
- 视频格式优先选择 H.264 与 AAC，提高常见浏览器和移动设备的兼容性
- 支持批量创建视频下载任务
- 成品由后端通过 `GET/HEAD` 文件接口下发，支持 Range 请求和 UTF-8 文件名
- 提供配置化的免费版与 Pro 清晰度请求、批量数量和功能限制
- 可选接入 Anthropic API，基于源视频已有字幕生成总结和字幕翻译
- 包含 URL/SSRF 检查、对外下载文件名与扩展名规范化、路径约束、磁盘余量检查和请求限流

平台支持范围由 yt-dlp 的 extractor 决定，会随网站规则变化；`GET /api/sites` 中的 `1800+` 是展示值，不代表每个平台始终可用。

## 技术栈

| 部分 | 技术 |
|---|---|
| 后端 | Python 3.13、FastAPI、Uvicorn、yt-dlp |
| 媒体处理 | ffmpeg |
| 前端 | Next.js 16、React 19、TypeScript 5、Tailwind CSS 4 |
| AI | Anthropic API（可选） |
| 存储 | 本地临时目录和进程内任务表，无数据库 |

## 快速开始

### 前置条件

- Python 3.13
- Node.js 20.9.0 或更高版本
- ffmpeg

确认 ffmpeg 可用：

```bash
ffmpeg -version
```

### 启动后端

```bash
cd backend
python3.13 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

后端必须使用单 worker。任务保存在当前进程内，多 worker 会导致状态轮询访问不到原任务。

### 启动前端

```bash
cd frontend
npm ci
npm run dev
```

打开 <http://localhost:3000>。前端默认请求 `http://127.0.0.1:8000`；如后端地址不同，在 `frontend/.env.local` 中设置：

```bash
NEXT_PUBLIC_API_BASE=https://api.example.com
```

前后端分开部署时，还需通过后端 `CORS_ORIGINS` 允许前端来源。项目未内置反向代理配置。

### 可选功能

- AI：在 `backend/.env` 设置 `ANTHROPIC_API_KEY`。源视频必须带有字幕或自动字幕。
- 登录站点：设置 Netscape 格式的 `COOKIE_FILE`，不支持 JSON cookie 导出。
- Pro 演示：默认兑换码定义在 `REDEEM_CODES`。项目没有真实支付或账号系统。

非本地环境必须将 `.env.example` 中的 `PRO_SECRET` 替换为安全随机值，否则令牌可被伪造。未配置固定 `PRO_SECRET` 时，服务重启会生成新密钥并使旧令牌失效。

## 下载流程

1. 前端调用 `POST /api/probe` 获取媒体信息、清晰度和字幕列表。
2. 前端调用 `POST /api/jobs` 或 `POST /api/batch` 创建任务。
3. 后端线程池重新探测链接，并通过 yt-dlp 下载、合并或转换媒体。
4. 前端每秒轮询 `GET /api/jobs/{id}`，状态依次可能为 `queued`、`probing`、`downloading`、`merging`、`converting`、`done` 或 `error`。
5. 任务完成后，浏览器访问 `GET /api/jobs/{id}/file` 保存文件。
6. 清理任务按创建时间删除超过阈值且未处于下载、探测、合并或转换阶段的任务；孤儿目录按目录修改时间回收。

## 项目结构

```text
backend/
  main.py         FastAPI 路由、生命周期和清理任务
  downloader.py   媒体探测、格式选择、下载和错误转换
  jobs.py         进程内任务表、线程池、进度状态和磁盘清理
  security.py     URL 检查、文件名处理和响应头
  pro.py          签名令牌、额度和进程内限流
  ai.py           可选的视频总结和字幕翻译
  config.py       环境变量和默认限制
  tests/          后端自动化测试
frontend/
  app/            Next.js 页面和全局样式
  components/     下载器、批量、AI、定价和展示组件
  lib/api.ts      后端客户端、令牌存储和任务轮询
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 返回服务、ffmpeg、AI、任务和磁盘状态 |
| `GET` | `/api/limits` | 返回当前令牌对应的功能限制 |
| `POST` | `/api/probe` | 解析链接并返回媒体信息 |
| `POST` | `/api/jobs` | 创建视频、音频或字幕任务 |
| `POST` | `/api/batch` | 批量创建任务并返回被拒绝的链接 |
| `GET` | `/api/jobs/{job_id}` | 查询任务状态和进度 |
| `GET/HEAD` | `/api/jobs/{job_id}/file` | 获取完成文件，支持 Range |
| `POST` | `/api/redeem` | 使用兑换码获取签名 Pro 令牌 |
| `POST` | `/api/ai/summary` | 生成视频总结，需要 Pro 和 AI 密钥 |
| `POST` | `/api/ai/translate` | 翻译字幕，需要 Pro 和 AI 密钥 |
| `GET` | `/api/sites` | 返回前端展示的平台列表 |

API 没有任务列表、取消、删除或重试接口。任务状态和文件接口以随机 `job_id` 作为访问凭证。

## 环境变量

后端从 `backend/.env` 读取配置。

| 变量 | 默认值 | 用途 |
|---|---:|---|
| `JOB_DIR` | `backend/_jobs` | 下载任务目录 |
| `JOB_TTL_SECONDS` | `3600` | 非运行状态任务的过期阈值，按任务创建时间计算 |
| `SWEEP_INTERVAL_SECONDS` | `300` | 清理任务执行间隔 |
| `MAX_WORKERS` | `4` | yt-dlp 下载线程数 |
| `MAX_FILESIZE_MB` | `2048` | yt-dlp 文件大小上限 |
| `MAX_DURATION_SECONDS` | `14400` | 可接受的媒体时长上限 |
| `MIN_FREE_DISK_MB` | `3072` | 接受新任务所需的最小剩余磁盘空间 |
| `SOCKET_TIMEOUT` | `30` | yt-dlp 网络超时秒数 |
| `FREE_MAX_HEIGHT` / `PRO_MAX_HEIGHT` | `720` / `2160` | 免费版和 Pro 优先请求的清晰度上限；无匹配格式时选择器可能回退到平台可用格式 |
| `FREE_BATCH_LIMIT` / `PRO_BATCH_LIMIT` | `1` / `20` | 单次批量任务数量上限 |
| `RATE_LIMIT_MAX` | `20` | 限流窗口内允许的请求次数 |
| `RATE_LIMIT_WINDOW_SECONDS` | `300` | 进程内限流窗口 |
| `PRO_SECRET` | 每次启动随机生成 | Pro 令牌签名密钥 |
| `PRO_TOKEN_TTL_DAYS` | `30` | Pro 令牌有效天数 |
| `REDEEM_CODES` | 内置演示码 | 逗号分隔的兑换码 |
| `ANTHROPIC_API_KEY` | 空 | Anthropic API 密钥 |
| `AI_MODEL` | `claude-sonnet-5` | AI 模型名 |
| `COOKIE_FILE` | 空 | Netscape 格式 cookies 文件路径 |
| `CORS_ORIGINS` | 本地 3000 端口 | 逗号分隔的允许来源 |
| `ALLOW_RESOLVED_NETS` | 空 | 仅开发环境使用的 DNS 网段例外 |
| `DEBUG` | `false` | 调试日志开关 |

前端变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://127.0.0.1:8000` | 浏览器访问的后端地址 |

## 验证

后端测试：

```bash
cd backend
./.venv/bin/python -m pytest tests -q
```

前端静态检查和生产构建：

```bash
cd frontend
npm run lint
npm run build
```

仓库没有项目自有的浏览器端测试或真实媒体下载测试。后端测试会替换媒体下载调用；部分 URL 安全测试仍需 DNS 解析公开域名。

## 当前限制

- 任务表、限流器和 Pro 状态均无持久化；服务重启会丢失任务，必须运行单个 Uvicorn worker。
- 文件不会在第一次下载后立即删除。清理器按任务创建时间回收超过 `JOB_TTL_SECONDS` 且未在运行的任务，孤儿目录按修改时间回收。
- 清晰度配置用于构建首选格式；当平台没有匹配格式时，yt-dlp 选择器可能使用不受该高度条件限制的最终回退格式。
- 默认前后端使用不同端口；生产环境需自行配置 HTTPS、反向代理、CORS 和进程守护。
- ffmpeg 缺失不会阻止服务启动，但高清合并、MP3 转换等任务可能失败；可通过 `/api/health` 检查。
- Pro 令牌是可转发的 bearer token，没有账号、支付、吊销列表或用户级资源隔离。
- 限流数据只保存在进程内，并读取第一个 `X-Forwarded-For` 值；部署时应由可信代理覆盖该请求头。
- AI 总结和翻译依赖源视频已有字幕或自动字幕；没有字幕时无法使用。
- 平台规则、地区限制、登录状态和 yt-dlp extractor 变化都会影响可用性。DRM 内容不受支持。

## 安全与使用须知

应用会拒绝本机、内网、链路本地、组播、保留字面量 IP 和常见云元数据地址，并约束文件服务路径、规范化对外下载文件名和扩展名。但 yt-dlp 会自行解析 DNS 和跟随重定向，应用层检查无法完整阻止 DNS rebinding 或重定向型 SSRF。公开部署时应同时使用容器或主机防火墙限制出站网络，并限制下载进程权限。

仅下载你拥有权利、已获授权、属于公共领域或符合法律允许范围的内容。请遵守媒体平台服务条款和所在地区的版权法律。

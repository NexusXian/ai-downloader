# 万能视频下载器

基于 FastAPI、yt-dlp 和 Next.js 的自托管视频下载应用。用户可以解析媒体链接，选择视频、MP3 或字幕任务，并在浏览器中查看进度和保存成品文件。

## 功能概览

- 解析媒体标题、封面、时长、可用清晰度和字幕语言
- 解析结果与 AI 总结并排显示：左侧下载选项，右侧总结，同屏可见；Pro 用户解析后自动开始总结
- 异步下载视频、提取 MP3、下载字幕，并显示进度、速度与剩余时间
- 视频格式优先选择 H.264 与 AAC，提高常见浏览器和移动设备的兼容性
- 支持批量创建视频下载任务
- 成品由后端通过 `GET/HEAD` 文件接口下发，支持 Range 请求和 UTF-8 文件名
- 提供配置化的免费版与 Pro 清晰度请求、批量数量和功能限制
- 可选接入 Anthropic API：总结优先使用创作者字幕或自动字幕，无可用字幕时回退到本地 `faster-whisper`
- AI 总结作为 Pro 异步任务运行，提供阶段、估算进度、Markdown 结果和导出；字幕翻译仍使用源字幕
- 总结完成后可按需生成思维导图：可缩放画布展示视频脉络，带时间戳的节点点击即跳转播放，支持 JSON、Markdown、OPML、SVG、PNG 导出
- 包含 URL/SSRF 检查、对外下载文件名与扩展名规范化、路径约束、磁盘余量检查和请求限流

平台支持范围由 yt-dlp 的 extractor 决定，会随网站规则变化；`GET /api/sites` 中的 `1800+` 是展示值，不代表每个平台始终可用。

## 技术栈

| 部分 | 技术 |
|---|---|
| 后端 | Python 3.13、FastAPI、Uvicorn、yt-dlp |
| 媒体处理 | ffmpeg |
| 前端 | Next.js 16、React 19、TypeScript 5、Tailwind CSS 4 |
| AI | Anthropic API、faster-whisper 本地语音识别（可选） |
| 存储 | 下载与 AI 总结各自的本地临时目录和进程内任务表，无数据库 |

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

后端必须使用单 worker。下载任务和 AI 总结任务都保存在当前进程内，多 worker 会导致状态轮询访问不到原任务；重启后总结状态不会恢复。

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

- AI 总结：在 `backend/.env` 设置 `ANTHROPIC_API_KEY`。后端先读取创作者字幕或自动字幕；没有可用字幕时下载临时音频并在本机运行 `faster-whisper`。
- `requirements.txt` 会安装 `anthropic`、`faster-whisper` 及本地推理依赖。第一次处理无字幕视频时会将所选 Whisper 模型下载到常规模型缓存；请预留下载时间、网络、缓存磁盘、RAM 和 CPU。默认 `small`、`cpu`、`int8` 与单个 AI worker 适合资源有限的初次部署，但转录仍可能较慢。
- AI 总结会把标题、来源 URL、带时间戳的转录文本发送给 Anthropic。临时源音频留在本机并在转录结束后立即尝试删除；删除失败会使任务失败，并由任务目录清理继续重试。
- 字幕翻译：需要 `ANTHROPIC_API_KEY` 和源视频字幕，不使用本地 Whisper 回退。
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

## AI 总结流程

1. 用户先兑换并使用 Pro 令牌；AI 总结的创建、状态、结果和导出路由都要求 Pro 认证。
2. 前端调用 `POST /api/ai/jobs`，后端检查 AI 配置、URL、磁盘和队列后返回 `202` 与 `job_id`。已配置 AI 且持有 Pro 令牌时，解析链接后前端自动发起这一步，无需再点一次按钮；免费用户仍需手动点击「生成总结」。重复解析同一条链接不会重复创建任务。
3. 独立 worker 优先读取可用字幕；字幕缺失或无法解析时下载临时音频并在本地运行 Whisper。
4. 后端把规范化转录发送给 Anthropic，长转录按片段分块处理，并在任务目录原子写入 `summary.md` 和 `transcript.json`。
5. 前端轮询 `GET /api/ai/jobs/{id}`。阶段可能为 `queued`、`extracting`、`transcribing`、`summarizing`、`done` 或 `error`；`percent` 单调增加，但只是阶段内估算值。
6. 完成后，前端读取 `GET /api/ai/jobs/{id}/result` 并通过认证请求下载 `GET /api/ai/jobs/{id}/export`。
7. 总结清理器按 `SUMMARY_JOB_TTL_SECONDS` 删除终态任务、产物和重启后留下的孤儿目录；运行中的任务不会被清理。

## 思维导图流程

1. 思维导图按需生成：总结完成后用户点击按钮，前端调用 `POST /api/ai/jobs/{id}/mind-map`。
2. 后端复用任务目录中已保存的 `transcript.json`，无需重新下载字幕或再跑一次 Whisper。
3. 模型只返回 JSON 树。后端校验层重新分配节点 `id`，限制深度（4 层）、节点数（100）、单层子节点数（12）和标签长度（80 字），并丢弃超出视频时长的时间戳。
4. 前端轮询同一个状态接口的 `mind_map_status`，完成后读取 `GET /api/ai/jobs/{id}/mind-map` 并用 markmap 渲染可缩放画布。
5. 带时间戳的节点可点击：已下载该视频时，点击会让页面内播放器跳到对应时间点，播放通过 `GET /api/jobs/{id}/play` 的 Range 请求实现。
6. 导图可导出 JSON、Markdown、OPML（后端序列化）和 SVG、PNG（浏览器序列化已渲染的画布）。生成失败只影响导图，总结结果保持可用。

## 项目结构

```text
backend/
  main.py         FastAPI 路由、生命周期和清理任务
  downloader.py   媒体探测、格式选择、下载和错误转换
  jobs.py         进程内任务表、线程池、进度状态和磁盘清理
  summary_jobs.py AI 总结与思维导图任务表、worker、临时结果和清理
  transcripts.py  字幕选择与解析、本地 Whisper 回退、转录序列化
  security.py     URL 检查、文件名处理和响应头
  pro.py          签名令牌、额度和进程内限流
  ai.py           Anthropic 总结、思维导图生成与校验、字幕翻译
  mind_map.py     思维导图 Markdown 与 OPML 序列化
  config.py       环境变量和默认限制
  tests/          后端自动化测试
frontend/
  app/            Next.js 页面和全局样式
  components/     下载器（含并排工作区）、批量、AI、思维导图、播放器、定价和展示组件
  lib/api.ts      后端客户端、令牌存储和任务轮询
  lib/mind-map.ts 导图节点转换、转义和文件名
  tests/          前端纯函数测试（node --test）
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
| `GET/HEAD` | `/api/jobs/{job_id}/play` | 以 inline 方式播放完成文件，供导图跳转，支持 Range |
| `POST` | `/api/redeem` | 使用兑换码获取签名 Pro 令牌 |
| `POST` | `/api/ai/jobs` | 创建异步总结任务，需要 Pro 和 AI 密钥 |
| `GET` | `/api/ai/jobs/{job_id}` | 查询总结阶段和估算进度，需要 Pro |
| `GET` | `/api/ai/jobs/{job_id}/result` | 获取完成的临时 JSON 结果，需要 Pro |
| `GET` | `/api/ai/jobs/{job_id}/export` | 下载完成的临时 Markdown 附件，需要 Pro |
| `POST` | `/api/ai/jobs/{job_id}/mind-map` | 为已完成的总结生成思维导图，需要 Pro 和 AI 密钥 |
| `GET` | `/api/ai/jobs/{job_id}/mind-map` | 获取校验后的思维导图 JSON，需要 Pro |
| `GET` | `/api/ai/jobs/{job_id}/mind-map/export` | 导出思维导图为 JSON、Markdown 或 OPML，需要 Pro |
| `POST` | `/api/ai/translate` | 翻译字幕，需要 Pro 和 AI 密钥 |
| `GET` | `/api/sites` | 返回前端展示的平台列表 |

API 没有任务列表、取消、删除或重试接口。普通下载的状态、文件和播放接口以随机 `job_id` 作为访问凭证；AI 总结与思维导图的创建、状态、结果和导出接口还会验证 `Authorization: Bearer` 或 `X-Pro-Token` 中的 Pro 令牌，所有 AI 读取接口均返回 `Cache-Control: no-store`。

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
| `SUMMARY_JOB_DIR` | `backend/_summary_jobs` | AI 总结、转录和思维导图产物的临时工作目录 |
| `SUMMARY_JOB_TTL_SECONDS` | `3600` | 已知非运行 AI 任务按 `created_at`、孤儿目录按目录 `st_mtime` 判断的过期阈值 |
| `AI_MAX_WORKERS` | `1` | 独立 AI worker 数；本地转录资源开销较高 |
| `AI_MAX_QUEUED_JOBS` | `10` | 排队和处理中 AI 任务的总容量 |
| `WHISPER_MODEL` | `small` | 无可用字幕时使用的模型；第一次使用会下载到本地缓存 |
| `WHISPER_DEVICE` | `cpu` | Whisper 推理设备 |
| `WHISPER_COMPUTE_TYPE` | `int8` | Whisper 推理计算类型 |
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
./.venv/bin/python -m pytest tests/test_api.py -q -k ai_job_end_to_end
```

前端静态检查和生产构建：

```bash
cd frontend
npm run lint && npm run build
```

检查补丁空白错误：

```bash
git diff --check
```

仓库没有项目自有的浏览器端测试或真实媒体下载测试。后端测试会替换媒体、Whisper 模型和 Anthropic 调用；聚焦的 `test_ai_job_end_to_end` 通过真实 API、Pro 兑换、AI worker、轮询和临时文件响应完成全流程，不访问网络、下载模型或调用付费 API。部分 URL 安全测试仍会使用系统 DNS 解析公开域名。

## 当前限制

- 下载和 AI 任务表、限流器均无持久化；服务重启会丢失任务状态，必须运行单个 Uvicorn worker。即使总结文件还在磁盘，重启后也不能通过原 `job_id` 恢复，孤儿目录会在 TTL 到期后清理。
- 下载文件不会在第一次获取后立即删除。下载清理器按任务创建时间回收超过 `JOB_TTL_SECONDS` 且未在运行的任务，孤儿目录按修改时间回收。
- AI 总结的 Markdown、转录 JSON 和思维导图 JSON 只临时保存在 `SUMMARY_JOB_DIR`；转录会保留到任务过期，供按需生成导图时复用。总结清理器使用 `SUMMARY_JOB_TTL_SECONDS`：已知非运行任务按 `created_at` 判断过期，重启后孤儿目录按目录 `st_mtime` 判断过期；失败任务会删除不完整结果。转录临时音频会在转录后立即尝试删除，删除失败时任务失败并由任务目录清理重试。
- 单个字幕响应上限为 10 MiB。规范化转录最多包含 50,000 个片段、1,000,000 个文本字符，且单个片段最多 12,000 个字符；超限字幕会继续尝试自动字幕或本地 Whisper，超限 Whisper 输出会安全失败。
- 清晰度配置用于构建首选格式；当平台没有匹配格式时，yt-dlp 选择器可能使用不受该高度条件限制的最终回退格式。
- 默认前后端使用不同端口；生产环境需自行配置 HTTPS、反向代理、CORS 和进程守护。
- ffmpeg 缺失不会阻止服务启动，但高清合并、MP3 转换等任务可能失败；可通过 `/api/health` 检查。
- Pro 令牌是可转发的 bearer token，没有账号、支付、吊销列表或用户级资源隔离。
- 限流数据只保存在进程内，并读取第一个 `X-Forwarded-For` 值；部署时应由可信代理覆盖该请求头。
- AI 总结依赖 Anthropic；没有可用字幕时依赖本地 Whisper，首次模型下载、CPU 转录、RAM 和模型缓存都可能成为资源瓶颈。字幕翻译仍要求源字幕。
- 平台规则、地区限制、登录状态和 yt-dlp extractor 变化都会影响可用性。DRM 内容不受支持。

## 安全与使用须知

应用会拒绝本机、内网、链路本地、组播、保留字面量 IP 和常见云元数据地址，并约束文件服务路径、规范化对外下载文件名和扩展名。但 yt-dlp 会自行解析 DNS 和跟随重定向，应用层检查无法完整阻止 DNS rebinding 或重定向型 SSRF。公开部署时应同时使用容器或主机防火墙限制出站网络，并限制下载进程权限。

AI 总结会把标题、来源 URL、转录文本和时间戳传输给 Anthropic，并受 Anthropic 的数据处理条款约束。Whisper 在后端本机处理临时音频，音频不会因总结功能上传给 Anthropic；转录完成后会删除音频。JSON 和 Markdown 在 TTL 内保存在服务器磁盘，虽然读取接口要求 Pro 且禁止缓存，但不应把本项目当作敏感内容的永久或加密存储。

仅下载你拥有权利、已获授权、属于公共领域或符合法律允许范围的内容。请遵守媒体平台服务条款和所在地区的版权法律。

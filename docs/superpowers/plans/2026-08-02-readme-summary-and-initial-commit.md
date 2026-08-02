# README Summary and Initial Commit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current README with an accurate Chinese project summary and create the repository's initial Git commit.

**Architecture:** Documentation will describe the existing FastAPI, yt-dlp, and Next.js application without changing application behavior. Git will track the full project source while excluding secrets, installed dependencies, build output, downloaded media, and local assistant settings.

**Tech Stack:** Python 3.13, FastAPI, yt-dlp, ffmpeg, Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, Git

## Global Constraints

- Keep `README.md` in Chinese.
- Use a practical summary rather than long implementation essays.
- Document only behavior supported by the current source tree.
- Include setup, features, download flow, architecture, API, configuration, verification, limitations, security, and legal-use notes.
- Do not change backend or frontend behavior.
- Commit backend source and tests, frontend source and lockfiles, root configuration, and documentation.
- Never commit `.env` files, virtual environments, dependencies, builds, downloaded files, caches, or `.claude/settings.local.json`.

---

### Task 1: Initialize a Safe Repository

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Consumes: Existing root project tree.
- Produces: A Git repository whose ignore rules protect all local-only files.

- [ ] **Step 1: Initialize Git**

Run:

```bash
git init
```

Expected: Git creates `.git/` in the project root.

- [ ] **Step 2: Exclude local assistant settings**

Add this root ignore rule:

```gitignore
.claude/settings.local.json
```

- [ ] **Step 3: Check protected paths**

Run:

```bash
git check-ignore backend/.env frontend/.env.local backend/.venv frontend/node_modules backend/_jobs .claude/settings.local.json
```

Expected: Every supplied path is printed as ignored.

### Task 2: Rewrite the Project README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Implemented routes in `backend/main.py`, job flow in `backend/jobs.py`, download behavior in `backend/downloader.py`, settings in `backend/config.py`, frontend flow in `frontend/components/downloader.tsx`, and API client behavior in `frontend/lib/api.ts`.
- Produces: A concise, code-backed project overview for developers and operators.

- [ ] **Step 1: Replace the README structure**

Use these sections in order:

```markdown
# 万能视频下载器
## 功能概览
## 技术栈
## 快速开始
## 下载流程
## 项目结构
## API
## 环境变量
## 验证
## 当前限制
## 安全与使用须知
```

- [ ] **Step 2: Document the current implementation**

State that the application probes links, creates asynchronous video/audio/subtitle jobs, polls job state, and serves completed files through the backend. Include batch downloads, Pro token redemption, optional Anthropic features, H.264/AAC preference, SSRF checks, filename safety, one-process in-memory jobs, `NEXT_PUBLIC_API_BASE`, ffmpeg, and the configured cleanup lifecycle.

- [ ] **Step 3: Remove unsupported claims**

Do not claim an exact test count, tracked Playwright coverage, tracked real-network end-to-end coverage, default same-origin delivery, per-user persistent quotas, payment integration, durable jobs, cancellation, retry endpoints, or multi-worker support.

- [ ] **Step 4: Check documentation consistency**

Run:

```bash
rg -n "147|Playwright|同源|完成后 1 小时|唯一接触 yt_dlp|免费额度按 IP" README.md
```

Expected: No output.

### Task 3: Verify and Commit the Full Project

**Files:**
- Stage: `.gitignore`, `README.md`, `backend/`, `frontend/`, `docs/superpowers/plans/`

**Interfaces:**
- Consumes: Completed repository and documentation changes.
- Produces: One verified initial commit containing the application source and documentation.

- [ ] **Step 1: Run backend tests**

Run:

```bash
backend/.venv/bin/python -m pytest backend/tests -q
```

Expected: All tests pass.

- [ ] **Step 2: Run frontend lint**

Run:

```bash
npm run lint
```

from `frontend/`.

Expected: ESLint exits successfully.

- [ ] **Step 3: Run the production build**

Run:

```bash
npm run build
```

from `frontend/`.

Expected: Next.js completes a production build successfully.

- [ ] **Step 4: Stage and inspect intended files**

Run:

```bash
git add .gitignore README.md backend frontend docs/superpowers/plans
git status --short
git diff --cached --stat
git diff --cached --check
```

Expected: Only source, tests, lockfiles, configuration, and documentation are staged; the final check prints no errors.

- [ ] **Step 5: Scan staged paths for secrets and generated content**

Run:

```bash
git diff --cached --name-only
```

Expected: No `.env`, `.venv`, `node_modules`, `.next`, `_jobs`, cache, or `.claude/settings.local.json` path appears.

- [ ] **Step 6: Create the initial commit**

Run:

```bash
git commit -m "feat: add video downloader"
```

Expected: Git creates the root commit successfully.

- [ ] **Step 7: Verify the committed state**

Run:

```bash
git status --short --branch
git log --oneline -1
```

Expected: The branch is clean and the latest commit is `feat: add video downloader`.

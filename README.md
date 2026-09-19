# 电商帮帮

AI 电商助手项目，包含 FastAPI + LangGraph 后端和 Next.js 前端。

## 演示视频

[查看演示视频](docs/test.mov)

## 本地开发

后端：

```bash
cd backend
uv sync
uv run uvicorn e_help.server:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## Docker 部署

项目根目录已经提供 Dockerfile、Nginx 和 Compose 配置：

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入模型、Milvus、PostgreSQL 配置
docker compose up -d --build
```

部署后访问 `http://服务器IP/`。完整的部署、更新、日志、回滚和故障排查步骤见 [docs/docker-deployment.md](docs/docker-deployment.md)。

## 动态 Skill

Skill 使用 `SKILL.md` 文件描述，支持本地目录和 HTTPS 地址。服务启动时会加载已安装 Skill；运行中可以通过管理 API 安装、启用、停用和删除，不需要重新发布服务。Skill 文件保存在 Docker 的 `e-help-skills` 持久卷中。

在 `backend/.env` 中设置 `SKILL_ADMIN_TOKEN`。一个最小的 `SKILL.md` 如下：

```markdown
---
name: after-sale-style
description: 售后问题的回答规范
version: "1.0"
---

回答售后问题时，先给结论，再给操作步骤；不确定时明确说明需要人工确认。
```

管理接口需要携带 `X-Admin-Token`，例如从 HTTPS 地址安装：

```bash
curl -X POST http://服务器IP/api/skills \
  -H 'Content-Type: application/json' \
  -H 'X-Admin-Token: 请替换为随机管理令牌' \
  -d '{"source":"https://example.com/skills/after-sale/SKILL.md"}'
```

网络 Skill 只接受 HTTPS，并限制为文本内容；本地 Skill 只能从 `SKILL_LOCAL_ROOTS` 配置的目录读取。

如果服务器不接收源码，可以在本地执行：

```bash
DOCKER_PLATFORM=linux/amd64 \
RELEASE_VERSION=v1.0.0 \
./scripts/package-release.sh
```

然后只把 `dist/` 下的三个镜像压缩包、Compose 文件、Nginx 配置和环境变量模板上传到服务器。服务器使用 `docker-compose.yml` 运行镜像，不需要安装源码。详细步骤见 [docs/docker-deployment.md](docs/docker-deployment.md)。

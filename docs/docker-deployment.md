# Docker 部署与项目管理

本文用于在一台安装了 Docker Engine 和 Docker Compose Plugin 的服务器上部署「电商帮帮」前后端。

## 1. 部署结构

```
浏览器
   |
   | :80
   v
Nginx
   |-- /       -> frontend:3000 (Next.js)
   |-- /api/*  -> backend:8000 (FastAPI)
   |
Docker bridge network: e-help
```

Nginx 是唯一对外暴露的容器。前端和后端只在 Docker 内部网络通信，后端使用 `backend/.env` 中的外部模型、Milvus 和 PostgreSQL 配置。

## 2. 首次部署：服务器直接构建

### 2.1 准备服务器

安装 Docker Engine 和 Docker Compose Plugin，并确认：

```bash
docker --version
docker compose version
```

将项目上传或拉取到服务器，例如：

```bash
git clone <项目地址> e-help
cd e-help
```

### 2.2 配置后端环境变量

不要把真实密钥写入 Git。首次部署时复制模板：

```bash
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，至少确认以下配置：

```dotenv
CLOSE_API_KEY=你的模型服务密钥
CLOSE_API_BASE_URL=你的模型服务地址
MILVUS_URI=你的Milvus地址
DB_URL=你的PostgreSQL连接串
```

当前项目依赖外部 Milvus 和 PostgreSQL，`docker-compose.yml` 不会自动创建这两个服务。它们需要已经运行，并且 Docker 容器可以访问对应地址。

如果要使用动态 Skill，建议同时设置：

```dotenv
SKILL_ADMIN_TOKEN=一段随机长令牌
SKILL_LOCAL_ROOTS=/app/initial-skills
```

Compose 已经为 `/app/skills` 配置持久卷。更新后端镜像或重建容器不会删除已安装的 Skill。

常见情况：

- 如果数据库在宿主机，不要填写 `127.0.0.1`，容器中的 `127.0.0.1` 指向后端容器自身。
- Linux 上可以使用宿主机局域网 IP，或按实际 Docker 网络配置使用宿主机地址。
- 如果数据库也运行在 Docker 中，应将数据库加入同一个网络，并使用数据库的服务名作为主机名。

### 2.3 构建并启动

在项目根目录执行：

```bash
docker compose up -d --build
```

查看容器状态：

```bash
docker compose ps
```

访问：

```text
http://服务器IP/
```

后端健康检查：

```bash
curl http://服务器IP/api/health
```

## 3. 推荐发布方式：本地构建，服务器只运行镜像

服务器不需要源码。镜像包在本地构建，服务器只接收发布产物：

```text
dist/
├── e-help-images-20260918-120000.tar.gz
├── docker-compose.yml
├── nginx.conf
└── backend.env.example
```

### 3.1 本地构建发布包

在项目根目录执行：

```bash
./scripts/package-release.sh

DOCKER_PLATFORM=linux/amd64 \
RELEASE_VERSION=v1.0.0 \
./scripts/package-release.sh
```

默认按 `linux/amd64` 构建，适合大多数 Linux 服务器。如果服务器是 ARM64：

```bash
DOCKER_PLATFORM=linux/arm64 ./scripts/package-release.sh
```

也可以指定版本名：

```bash
RELEASE_VERSION=v1.0.0 ./scripts/package-release.sh
```

脚本会构建后端、前端和 Nginx 镜像，并导出为一个压缩包。注意，本地需要启动 Docker Desktop 或 Docker Engine。

### 3.2 上传发布产物

只上传 `dist/` 里的发布文件，不上传源码：

```bash
scp dist/e-help-images-*.tar.gz user@服务器:/opt/e-help/
scp dist/docker-compose.yml user@服务器:/opt/e-help/
scp dist/nginx.conf user@服务器:/opt/e-help/
scp dist/backend.env.example user@服务器:/opt/e-help/
```

首次部署时，在服务器创建真实环境变量文件：

```bash
cd /opt/e-help
cp backend.env.example backend.env
vi backend.env
```

`backend.env` 只放服务器上的真实密钥和数据库连接信息，不要上传到 Git。

### 3.3 服务器导入并启动

```bash
cd /opt/e-help
docker load < e-help-images-20260918-120000.tar.gz
docker compose up -d
docker compose ps
curl http://127.0.0.1/api/health
```

服务器上的 Compose 文件没有 `build:` 配置，因此不会读取或要求项目源码。

### 3.4 发布新版本

本地重新打包并上传新的 `tar.gz`、`docker-compose.yml` 和 `nginx.conf`：

```bash
docker load < e-help-images-v1.1.0.tar.gz
docker compose up -d
```

Docker 会使用导入的新镜像重建对应容器。服务器上的 `backend.env` 不会被发布包覆盖。

### 3.5 回滚

保留历史镜像包即可回滚：

```bash
docker load < e-help-images-v1.0.0.tar.gz
docker compose up -d
```

如果使用固定的 `release` 标签，导入哪个包，当前服务就会使用哪个版本。

## 4. 常用管理命令

### 查看日志

```bash
docker compose logs -f
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f nginx
```

### 查看状态和资源

```bash
docker compose ps
docker stats
```

### 重启服务

重启全部服务：

```bash
docker compose restart
```

只重启某个服务：

```bash
docker compose restart backend
```

### 修改环境变量后生效

修改 `backend/.env` 后，重建后端容器：

```bash
docker compose up -d --no-deps --build backend
```

修改 Nginx 配置后：

```bash
docker compose up -d --no-deps nginx
```

### 更新代码

```bash
git pull
docker compose up -d --build
docker compose ps
```

建议更新后检查：

```bash
curl http://服务器IP/api/health
docker compose logs --tail=100 backend
```

### 停止和删除容器

停止服务但保留容器：

```bash
docker compose stop
```

停止并删除容器、网络：

```bash
docker compose down
```

删除容器、网络和本项目构建镜像：

```bash
docker compose down --rmi local
```

本项目没有在 Compose 中声明数据库卷，因此 `down` 不会删除外部 Milvus 或 PostgreSQL 数据。

## 5. 版本发布与回滚

推荐使用 Git 标签发布：

```bash
git fetch --tags
git checkout v1.0.0
docker compose up -d --build
```

回滚到上一个版本：

```bash
git checkout v0.9.0
docker compose up -d --build
```

如果服务器上有本地修改，先备份 `backend/.env`，不要用会覆盖本地文件的强制切换命令。

## 6. 生产环境建议

1. 将服务器防火墙只开放 `80`，如果配置 HTTPS，再开放 `443`；不要对公网开放 `8000` 和 `3000`。
2. 为 Nginx 配置域名和 HTTPS 证书。当前配置是 HTTP 最小配置，适合先验证部署。
3. 定期备份 PostgreSQL 和 Milvus 数据。
4. 使用日志轮转，避免容器日志长期占满磁盘：

```bash
docker system df
docker image prune
```

5. 不要提交 `backend/.env`。项目根目录 `.gitignore` 已忽略 dotenv 文件。
6. 如果后端启动较慢，保持 Compose 中 backend healthcheck 的 `start_period` 足够长；只有健康检查通过后，前端和 Nginx 才会启动。

## 7. 故障排查

### Nginx 返回 502

```bash
docker compose ps
docker compose logs --tail=200 nginx backend frontend
```

重点检查：

- `backend` 是否健康；
- `frontend` 是否已经启动；
- `backend/.env` 中的外部服务地址是否可从容器访问；
- 是否误把外部数据库地址写成了 `127.0.0.1`。

### `/api/health` 返回 502 或后端不断重启

```bash
docker compose logs -f backend
```

通常是模型服务、Milvus 或 PostgreSQL 地址/密钥错误。先修正 `backend/.env`，再执行：

```bash
docker compose up -d --no-deps --build backend
```

### 聊天请求一直没有流式输出

检查 Nginx 日志和后端日志：

```bash
docker compose logs -f nginx backend
```

配置中的 `/api/chat/stream` 已关闭 Nginx 缓冲并设置了较长读取超时。如果仍无输出，优先确认模型服务能够从后端容器访问。

### 前端构建失败

查看完整构建输出：

```bash
docker compose build --no-cache frontend
```

本项目的 Next.js 使用 standalone 输出，构建阶段会运行 `npm run build`，运行阶段只携带必要的生产文件。

## 8. 本地验证

在提交部署前，可在项目根目录执行：

```bash
docker compose config
docker compose build
docker compose up -d
curl http://127.0.0.1/api/health
docker compose ps
```

验证完成后停止本地服务：

```bash
docker compose down
```

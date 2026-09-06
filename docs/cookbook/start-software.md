# 启动 DramaClaw CE

导航：[Cookbook 首页](README.md) · [共享系统地图](system-map.md) · [常见故障](../zh/guides/troubleshooting.md) · [模型配置](../zh/getting-started/configuring-models.md)

本文说明如何从源码启动 DramaClaw CE。正常使用推荐 Docker Compose；需要修改和调试代码时使用本地开发模式。完整安装和部署选择见[安装说明](../zh/getting-started/installation.md)与[自托管指南](../zh/guides/self-hosting.md)，这里集中记录开发时常用的启动、端口和诊断命令。

以下命令默认从仓库根目录执行；文中的 `<repo-root>` 表示仓库根目录。

## 方式一：Docker Compose（推荐）

Docker 会同时启动后端 API 和网页前端，并在容器内提供 `ffmpeg`。本机不需要 PostgreSQL、Redis、Celery 或 GPU。

### 1. 检查 Docker

先启动 Docker Desktop，再执行：

```bash
docker --version
docker compose version
```

两条命令都能输出版本号后再继续。

### 2. 准备配置

```bash
cd <repo-root>
cp .env.example .env
```

打开 `.env`，至少把下面的默认密码改掉：

```dotenv
PROMPT_EXPORT_PASSWORD=换成一个随机且不复用的密码
```

不要把包含密码或模型密钥的 `.env` 提交到 Git。

### 3. 启动

```bash
docker compose up -d --build
```

首次启动需要构建镜像和下载依赖，耗时会比后续启动长。命令返回后检查服务状态：

```bash
docker compose ps
```

`api` 和 `web` 都处于运行状态后，访问：

- 网页界面：<http://localhost:8080>
- REST API：<http://localhost:8780>
- API 健康检查：<http://localhost:8780/api/v1/config>

也可以在终端检查 API：

```bash
curl -f http://localhost:8780/api/v1/config
```

### 4. 首次配置模型

网页打开后，进入「设置 → 模型配置 → 官方渠道」，填写 DC key 并保存。模型推理通过远程网关执行；只启动界面和 API 时可以暂不填写，但文本、图片、视频和音频生成功能会失败。

如果需要使用自己的本地 NewAPI 网关，改用仓库中的 self-hosted Compose 配置；相关说明见[模型配置](../zh/getting-started/configuring-models.md)。

### 5. 查看日志、重启和停止

```bash
# 查看后端日志
docker compose logs -f api

# 查看全部服务日志
docker compose logs -f

# 重启
docker compose restart

# 停止并删除容器，保留数据卷
docker compose down
```

不要执行 `docker compose down -v`，其中的 `-v` 会删除保存项目状态和媒体数据的 `ce-data` 数据卷。

代码更新后重新构建并启动：

```bash
git pull
docker compose up -d --build
```

## 方式二：本地开发模式

本地开发会分别启动 Python 后端和 Vite 前端。需要：

- Python 3.11 或 3.12
- `uv`
- `pnpm` 11.5.0
- `ffmpeg`
- `curl`

仓库提供了统一启动脚本，它会在依赖目录不存在时自动安装依赖，并在退出时关闭前后端进程。

```bash
cd <repo-root>
test -f .env || cp .env.example .env
bash scripts/start-ce.sh
```

启动完成后访问：

- 网页界面：<http://localhost:5173>
- REST API：<http://localhost:8780>
- API 健康检查：<http://localhost:8780/api/v1/config>

在运行脚本的终端按 `Ctrl+C` 可同时停止前后端。

如果不使用统一脚本，可以打开两个终端分别启动。

终端一：

```bash
cd <repo-root>
uv sync --group dev
uv run novelvideo api --host 0.0.0.0 --port 8780
```

终端二：

```bash
cd <repo-root>/frontend
test -f .env || cp .env.example .env
pnpm install
pnpm dev --host 0.0.0.0 --port 5173
```

## 端口冲突

Docker 模式下，可以在启动命令前指定其他宿主端口：

```bash
ST_WEB_PORT=8081 ST_API_PORT=8781 docker compose up -d --build
```

此时网页地址是 <http://localhost:8081>，API 地址是 <http://localhost:8781>。

本地开发脚本可以这样改端口：

```bash
NOVELVIDEO_API_PORT=8781 SUPERTALE_FE_PORT=5174 bash scripts/start-ce.sh
```

如果不确定端口被谁占用：

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -nP -iTCP:8780 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

## 常见问题

### Docker 服务启动后立即退出

先查看容器状态和后端日志：

```bash
docker compose ps -a
docker compose logs --tail=200 api
```

### API 能访问，但生成功能失败

确认已经在网页的「设置 → 模型配置」中保存并启用了可用渠道；同时检查网络是否能访问对应模型网关。涉及本地参考图片时，还需要配置 `.env` 中的媒体 relay 参数。

### 本地开发提示 Python 版本不支持

项目要求 Python `>=3.11,<3.13`。可以让 `uv` 使用 Python 3.12：

```bash
uv python pin 3.12
uv sync --group dev
```

### `pnpm` 无法使用

项目声明的版本是 `pnpm@11.5.0`。使用 Corepack 安装并激活：

```bash
corepack enable
corepack prepare pnpm@11.5.0 --activate
```

更完整的故障说明见[常见故障](../zh/guides/troubleshooting.md)。

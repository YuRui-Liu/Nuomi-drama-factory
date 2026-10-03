# Nuomi 单团队部署

适用范围：一个工作室，管理员创建成员，按项目分享。`ST_EDITION=team` 是本仓库内置实现，不需要原项目的 EE 扩展。CE 的启动文件保持独立。

## 资源与访问入口

已知目标：腾讯云北京轻量应用服务器 `82.156.213.142`；域名 `yrui9.online`。建议网站使用 `studio.yrui9.online`。

- 正式团队使用建议至少 2 核 / 4GB；当前 2GB 仅作为受限验证环境，尚无容量保证。视频合成会争抢 CPU 和内存。
- 北京地域通过域名开办网站，先完成 ICP 备案：[腾讯云说明](https://cloud.tencent.com/document/product/1207/45756)。
- DNS 添加 `studio` 的 A 记录指向服务器公网 IP。只有服务器实际启用 IPv6 时才添加 AAAA。
- 腾讯云防火墙和系统防火墙开放 TCP 80、443；SSH 仅对管理员来源开放。不要开放 5432、8780、8080。
- Caddy 通过域名自动申请 HTTPS 证书；DNS、80/443 和公网连通都必须正常。
- 本文交付部署配置，未表示目标服务器已经安装或上线。

## 部署结构

`edge`（Caddy）→ `web`（前端）及 `api`（FastAPI）；`api` → `db`（PostgreSQL）。只有 edge 发布主机端口。API 可以访问外部模型服务；数据库只在内部网络。

账号、会话、项目登记和授权在 PostgreSQL；项目正文、SQLite 业务数据、素材、模型设置在 `team-data` 卷。数据库备份不能替代素材卷备份。

第一版使用单个 API 进程和现有 inline 任务执行器。不要自行增加 API 副本或启动多进程 worker；长任务升级前应停止提交并等待结束。

## 首次安装

在服务器安装 Docker Engine 和 Compose v2，并将**包含本次团队功能的代码版本**部署到服务器工作目录。默认分支未必已经包含这些改动，不能只 clone 原版本就期待存在 team 功能。以下命令均在项目根目录运行。

```bash
cp deploy/team/environment.example .env.team
chmod 600 .env.team
openssl rand -hex 32
```

把生成的十六进制随机值填入 `.env.team` 的 `TEAM_DB_PASSWORD`；保留 `TEAM_DOMAIN=studio.yrui9.online`。该密码用于数据库，不能代替网站账号密码。不要把 `.env.team` 提交 Git。数据库卷初始化后不要单独改密码变量，否则应用与数据库凭据会不一致。

```bash
docker compose --env-file .env.team -f docker-compose.team.yml config --quiet
docker compose --env-file .env.team -f docker-compose.team.yml build api web
docker compose --env-file .env.team -f docker-compose.team.yml up -d db
docker compose --env-file .env.team -f docker-compose.team.yml run --rm api python -m novelvideo.team.cli init-db
docker compose --env-file .env.team -f docker-compose.team.yml run --rm api python -m novelvideo.team.cli create-admin --username admin
```

最后一个命令交互输入初始管理员密码，12–128 个字符，无默认密码。用户名 3–64 个字符，仅字母、数字、下划线和连字符，首字符为字母或数字。

```bash
docker compose --env-file .env.team -f docker-compose.team.yml up -d
docker compose --env-file .env.team -f docker-compose.team.yml ps
docker compose --env-file .env.team -f docker-compose.team.yml logs --tail=100 api edge
```

访问 `https://studio.yrui9.online`，用初始管理员登录；在顶部成员管理入口创建成员，在项目分享中添加授权。

首次构建包含 Python、前端和媒体处理依赖，2GB 服务器可能无法完成。可以在资源更充足、CPU 架构与服务器一致的构建机生成镜像后传到服务器；目前没有预发布的团队镜像供直接拉取。

## 模型配置和功能边界

- 管理员在现有模型设置中配置文本、图像、视频服务。密钥留在后端，成员不能读取或修改全局配置。
- 团队聊天 Agent 支持 Codex、WorkBuddy、DeepSeek Harness，也保留 Hermes。聊天与文本任务各自独立配置。这里的“本地 CLI”运行在部署 Nuomi 的服务器上，由管理员统一配置账号；成员从网页提交任务，不需要 SSH。尚不包含每个成员电脑上的远程 CLI 执行节点。
- 聊天复用原有界面，通过短期 Agent 凭据调用获授权项目 API。三种 CLI 由 Nuomi 管理对话历史和工具循环，按用户、项目、后端与模型隔离上下文；每次推理和工具请求检查权限。CLI 不接收 Agent API 凭据，也不获得服务器通用终端权限。回复按推理步骤返回，并非 CLI 原生逐 token 流式传输。
- CLI 只得到本次任务的临时目录和所选运行时的认证信息，不继承数据库连接或其他项目目录。Linux 必须能运行 bubblewrap；沙箱启动失败时任务报错，不退回无隔离执行。
- 四种项目角色：所有者可执行项目生命周期操作；管理员可分享授权；编辑者可创作；查看者只读。
- 实例管理员负责账号和配置，但不自动越过其他成员的项目权限。成员可以创建自己的项目，再授权给别人。
- 停用账号或重置密码会撤销该账号的浏览器和 Agent 会话；退出登录也撤销该账号的 Agent 凭据。撤销项目授权后，新请求立即拒绝，正在进行的聊天在下一次 10 秒复查时取消，任务 SSE 在下一次 30 秒复查时停止。
- 登录按连接来源与规范化账号分别限流，每分钟最多 20 次；位于同一反向代理后的不同成员不共用同一个账号限额。该内存限流仅适用于本部署的单 API 进程。
- 撤销访问不会自动取消已经提交的生成任务；有权限的编辑者可在任务界面取消。
- 第一版素材通过鉴权 API 从持久卷读取，**尚未接入腾讯云 COS**。不要把 `/data` 配成公共静态目录。50GB 磁盘与 4Mbps 带宽仍是容量限制。
- 这是角色授权与共享项目，不是多人实时合写；同一内容同时编辑仍需团队协调。

## 启用聊天与本地 CLI

团队镜像包含 Hermes、bubblewrap 和固定版本 Codex（`TEAM_CODEX_VERSION`）。构建仍需访问对应安装源。当前只验证了本地代码与测试，尚未在目标 Linux 主机启动这些容器。

1. 管理员登录网页，打开「设置 → 聊天 Agent」，选择 Codex、WorkBuddy、DeepSeek Harness 或 Hermes，填写对应模型并保存，下一轮对话生效。「文本任务运行时」独立配置。Hermes 继续使用模型网关；其余后端需要下述 CLI 安装与认证。配置保存在持久状态目录的 SQLite 中，优先于 `DRAMACLAW_CHAT_BACKEND` 等环境默认值。
2. Codex 优先通过 `.env.team` 的 `OPENAI_API_KEY` 配置。需要账号登录时，由管理员在容器中运行下方登录命令；`CODEX_HOME=/data/cli-auth/codex` 存放在持久卷中。不要挂载整台电脑的主目录。任务使用认证文件的临时副本，刷新结果不回写共享文件；账号认证失效时需要管理员重新登录，当前不包含共享 OAuth 刷新协调。账号登录是否可用取决于当前 Codex CLI 和账号提供的认证方式。
3. WorkBuddy、DeepSeek Harness 的适配器已经支持团队隔离，但默认镜像未安装这两个工具。使用前须将对应 Linux CLI 及依赖安装到自定义 API 镜像，在后端配置 `WORKBUDDY_BIN` / `DSH_BIN`，并提供 `CODEBUDDY_API_KEY` / `DEEPSEEK_API_KEY`。不要把 macOS 的二进制直接复制到 Linux。团队模式不复用交互式 CLI 的完整配置、历史或插件。
4. 在实际容器内运行隔离自检，通过后再用两个成员、两个项目测试聊天与 CLI。Linux 内核、容器 seccomp/AppArmor 和用户命名空间策略都可能阻止 bubblewrap；出现拒绝时需修复运行环境。不要为绕过检查直接改成 `privileged` 或禁用隔离。

```bash
docker compose --env-file .env.team -f docker-compose.team.yml exec api codex --version
docker compose --env-file .env.team -f docker-compose.team.yml exec api codex login --device-auth
docker compose --env-file .env.team -f docker-compose.team.yml exec api python -m novelvideo.team.execution
```

隔离自检不调用收费模型。正式验收仍需真实执行一次聊天和每种启用的 CLI 任务。共享 CLI 凭据会提供给相应 CLI 进程；文件隔离不意味着该进程无法使用或读取自己的认证信息。仅向可信团队开放，不把共享高权限账号视为按成员独立计费的凭据系统。

2GB 主机先将任务并发限制为 1，并限制同时活跃的聊天人数；容量应以实际峰值内存测试为准，不能凭配置保证多人生成稳定。

## 导入已有 CE 项目

先停止旧实例写入并备份其数据库、output、state、runtime。不要把团队数据库连接指向旧 CE 实例；迁移读取 CE 的项目登记数据库，不修改原库。

建议先在备份副本中演练。把旧文件复制到团队卷中，并让旧登记中的项目目录在容器内有对应路径。迁移命令只迁移登记，不复制素材；不要仅导入数据库而遗漏实际目录。

```bash
docker compose --env-file .env.team -f docker-compose.team.yml stop api
docker compose --env-file .env.team -f docker-compose.team.yml run --rm api python -m novelvideo.team.cli migrate-ce --owner admin --source /data/state/local/projects.db
docker compose --env-file .env.team -f docker-compose.team.yml up -d api
```

上述无映射命令适合旧目录已经是 `/data/output`、`/data/state`、`/data/runtime` 的情况。本地 macOS 绝对路径不能直接用于 Linux；使用迁移命令的 `--path-map OLD=NEW` 将每个旧目录前缀映射到相应容器目录。先运行 `python -m novelvideo.team.cli migrate-ce --help` 查看参数。迁移后核对项目数量、历史素材播放、项目归属，并以第二个账号验证授权前不可见、分享后可读。

## 备份、恢复和更新

暂停写入后同时备份 PostgreSQL 和文件卷。以下备份会包含业务数据与密钥，目录权限需限制。

```bash
mkdir -p backups
chmod 700 backups
docker compose --env-file .env.team -f docker-compose.team.yml stop api
docker compose --env-file .env.team -f docker-compose.team.yml exec -T db pg_dump -U nuomi -d nuomi -Fc > backups/team-db.dump
docker compose --env-file .env.team -f docker-compose.team.yml run --rm --no-deps -T api tar -C /data -czf - . > backups/team-data.tar.gz
docker compose --env-file .env.team -f docker-compose.team.yml up -d api
```

每次备份放入独立日期目录，避免覆盖唯一副本；另行安全保管 `.env.team` 和部署代码版本。备份应复制到服务器之外。

恢复时使用**新的空数据库卷和文件卷**，先按首次安装配置相同版本及密码并启动 db，然后恢复：

```bash
docker compose --env-file .env.team -f docker-compose.team.yml exec -T db pg_restore -U nuomi -d nuomi --exit-on-error < backups/team-db.dump
docker compose --env-file .env.team -f docker-compose.team.yml run --rm --no-deps -T api tar -C /data -xzf - < backups/team-data.tar.gz
docker compose --env-file .env.team -f docker-compose.team.yml up -d
```

不要在有业务数据的卷上直接执行上述恢复，也不要用 `down -v` 做普通更新。更新前记录当前代码版本、备份并等待任务结束；拉取指定版本后重新 build、up。需要回滚时，使用旧代码版本和同一时刻的数据库/素材备份一起恢复。

## 上线验收

1. 管理员能登录、退出，刷新后身份正确。
2. 创建成员，成员不能进入账号/模型管理。
3. 管理员建项目：成员授权前看不到，查看者授权后可读但不能生成或删除。
4. 改为编辑者后可以提交创作任务，任务显示提交人。
5. 撤销分享后，项目和直接素材 URL 均拒绝访问。
6. 停用成员或重置密码后，旧登录会话失效。
7. 执行一次小规模真实生成和视频播放，再调整并发；此项会消耗模型额度。
8. 重启容器后账号、项目、素材仍在；在另一空环境完成一次备份恢复演练。

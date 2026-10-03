FROM python:3.12-slim

# 项目全程用 uv 管理(与 host 一致)。Dockerfile 也用 uv,使 uv.lock 锁版本 +
# [[tool.uv.dependency-metadata]] override(da2 的 torch==2.5.0 冲突、sharp 的 gsplat)
# 生效——pip 不认这些 override,会在 da2/world 解析冲突时 build 失败。
RUN pip install --no-cache-dir uv

ENV ST_EDITION=ce \
    ST_CONTROL_PLANE_DSN= \
    ST_REDIS_URL= \
    ST_CELERY_BROKER_URL= \
    ST_CELERY_RESULT_BACKEND= \
    NOVELVIDEO_DATA_ROOT=/data \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    HERMES_CLI_PATH=/root/.local/bin/hermes

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg bubblewrap \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# license 正文按 REUSE 惯例只存于 LICENSES/(pyproject license-files 指向它),
# hatchling 构建 wheel 时需要这份文件在上下文中。
COPY LICENSES ./LICENSES
COPY src ./src
COPY .hermes ./.hermes

# Check assets shipped by this repository; legacy login audio/videos are absent.
RUN test -f src/novelvideo/assets/login_bg.png \
    && test -f src/novelvideo/assets/to_be_continued.png

# 可选 3DGS/SHARP「world」特性。默认精简镜像。INSTALL_WORLD=1 时:
#   - node + @playcanvas/splat-transform(PLY→SOG,MIT)装到 PATH
#   - uv sync --extra world(torch/sharp@apple/ml-sharp/da2/…;经 uv override 去 gsplat
#     + 化解 da2 torch 冲突,与 host `uv sync --extra world` 完全一致)
# 模型权重不烤进镜像:运行时自动下载到可写卷(Apple 研究许可,绝不再分发)。
# 注:slim base 为 CPU;GPU 加速需 CUDA base + nvidia runtime。
ARG INSTALL_WORLD=0
RUN set -eux; \
    if [ "$INSTALL_WORLD" = "1" ]; then \
        apt-get update; \
        apt-get install -y --no-install-recommends git nodejs npm; \
        rm -rf /var/lib/apt/lists/*; \
        npm install -g @playcanvas/splat-transform; \
        uv sync --frozen --no-dev --extra world; \
    else \
        uv sync --frozen --no-dev; \
    fi; \
    mkdir -p /data

RUN uv tool install 'hermes-agent[acp]'

# Optional pinned CLI for team text tasks. Never bake credentials into an image.
ARG TEAM_CODEX_VERSION=""
RUN if [ -n "$TEAM_CODEX_VERSION" ]; then \
        apt-get update && apt-get install -y --no-install-recommends nodejs npm \
        && npm install -g "@openai/codex@$TEAM_CODEX_VERSION" \
        && rm -rf /var/lib/apt/lists/*; \
    fi

ENV PATH="/app/.venv/bin:/root/.local/bin:$PATH"

EXPOSE 8780
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8780/api/v1/config', timeout=2).status == 200 else 1)"

CMD novelvideo api --host 0.0.0.0 --port 8780

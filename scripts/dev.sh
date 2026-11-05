#!/usr/bin/env bash
# 开发入口：Vite 自带前端 HMR，同时开启 Uvicorn 后端热重载。
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export NOVELVIDEO_API_RELOAD="${NOVELVIDEO_API_RELOAD:-1}"
exec bash "$root_dir/scripts/start-local.sh" "$@"

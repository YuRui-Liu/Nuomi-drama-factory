#!/usr/bin/env bash
# Usage: bash scripts/start-local.sh [--check|--help]
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
parent_dir="$(dirname "$root_dir")"
novelvideo_bin="${NOVELVIDEO_BIN:-$parent_dir/.venv/bin/novelvideo}"
export ST_EDITION=ce
export NOVELVIDEO_DATA_ROOT="${NOVELVIDEO_DATA_ROOT:-$parent_dir/nuomi-drama-data}"
export NOVELVIDEO_STATE_DIR="${NOVELVIDEO_STATE_DIR:-$NOVELVIDEO_DATA_ROOT/state}"
export VITE_API_URL=http://127.0.0.1:8780

case "${1:-}" in
  --help|-h)
    echo "用法: bash $0 [--check|--help]"
    echo "启动 API (127.0.0.1:8780) 和前端 (127.0.0.1:5173)，Ctrl+C 停止。"
    echo "可覆盖环境变量: NOVELVIDEO_BIN、NOVELVIDEO_DATA_ROOT、NOVELVIDEO_STATE_DIR"
    echo "--check 仅检查依赖和显示配置，不启动服务。"
    exit 0
    ;;
  ""|--check) ;;
  *) echo "未知参数: $1（使用 --help 查看用法）" >&2; exit 2 ;;
esac

if [[ ! -x "$novelvideo_bin" ]]; then
  echo "找不到可执行文件: $novelvideo_bin，请设置 NOVELVIDEO_BIN。" >&2
  exit 2
fi
if ! command -v pnpm >/dev/null 2>&1; then
  echo "找不到 pnpm，请先安装并加入 PATH。" >&2
  exit 2
fi
if [[ ! -d "$root_dir/frontend/node_modules" ]]; then
  echo "前端依赖未安装，请先运行: cd \"$root_dir/frontend\" && pnpm install" >&2
  exit 2
fi

printf '后端程序: %s\n数据目录: %s\n状态目录: %s\n' \
  "$novelvideo_bin" "$NOVELVIDEO_DATA_ROOT" "$NOVELVIDEO_STATE_DIR"
[[ "${1:-}" != --check ]] || exit 0

# Give each service its own process group, including pnpm's Vite children.
# This also works with macOS's bundled Bash 3.2 (no wait -n required).
set -m
api_pid=""
frontend_pid=""
cleanup() {
  trap - EXIT INT TERM
  echo "正在停止前后端服务……"
  for pid in "$frontend_pid" "$api_pid"; do
    if [[ -n "$pid" ]]; then
      kill -TERM -- "-$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$root_dir"
"$novelvideo_bin" api --host 127.0.0.1 --port 8780 &
api_pid=$!
(
  cd "$root_dir/frontend"
  exec pnpm dev --host 127.0.0.1 --port 5173 --strictPort
) &
frontend_pid=$!

echo "启动中，服务就绪后访问: http://127.0.0.1:5173"
echo "API: http://127.0.0.1:8780；按 Ctrl+C 同时停止前后端。"
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
echo "有服务退出，请查看上方日志。" >&2
exit 1

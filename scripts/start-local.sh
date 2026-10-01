#!/usr/bin/env bash
# Usage: bash scripts/start-local.sh [--check|--help]
# 重启语义：启动前会先停止上一次由本脚本（或本仓库）启动的 API 与前端，
# 因此重复执行本脚本就等于“重启服务”。
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
parent_dir="$(dirname "$root_dir")"
novelvideo_bin="${NOVELVIDEO_BIN:-$parent_dir/.venv/bin/novelvideo}"
api_port="${NOVELVIDEO_API_PORT:-8780}"
frontend_port="${SUPERTALE_FE_PORT:-5173}"
stop_timeout="${START_LOCAL_STOP_TIMEOUT:-15}"
api_ready_timeout="${NOVELVIDEO_API_READY_TIMEOUT:-90}"
# PID 文件按“端口对”区分：否则用非默认端口启动时会读到默认端口的记录，
# 把另一个实例（例如你终端里正在跑的那套）误停。
pid_file="$root_dir/runtime/start-local-${api_port}-${frontend_port}.pid"
export ST_EDITION=ce
export NOVELVIDEO_DATA_ROOT="${NOVELVIDEO_DATA_ROOT:-$parent_dir/nuomi-drama-data}"
export NOVELVIDEO_STATE_DIR="${NOVELVIDEO_STATE_DIR:-$NOVELVIDEO_DATA_ROOT/state}"
export VITE_API_URL="http://127.0.0.1:${api_port}"

has_lsof=1
command -v lsof >/dev/null 2>&1 || has_lsof=0
has_ps=1
command -v ps >/dev/null 2>&1 || has_ps=0

usage() {
  cat <<EOF
用法: bash $0 [--check|--help]
启动 API (127.0.0.1:${api_port}) 和前端 (127.0.0.1:${frontend_port})，Ctrl+C 停止。
启动前会先停止上一次由本脚本/本仓库启动的服务，重复执行即为重启。
只停止属于本仓库的进程；端口被其他程序占用时会中止并提示，不会误杀。
API 就绪后才启动前端（避免 Vite 代理先报 ECONNREFUSED）。
可覆盖环境变量: NOVELVIDEO_BIN、NOVELVIDEO_DATA_ROOT、NOVELVIDEO_STATE_DIR、
                NOVELVIDEO_API_PORT、SUPERTALE_FE_PORT、START_LOCAL_STOP_TIMEOUT、
                NOVELVIDEO_API_READY_TIMEOUT
--check 仅检查依赖、显示配置和端口占用，不启动也不停止任何服务。
EOF
}

# 监听指定端口的 PID 列表（lsof 不可用或无人监听时返回空，且始终返回 0：
# lsof 在“无匹配”时退出码为 1，配合 set -o pipefail 会误伤调用方）。
port_listener_pids() {
  [[ "$has_lsof" == 1 ]] || return 0
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | sort -u || true
}

# 进程组 ID（用于连着 pnpm -> vite 这类父子进程一起停）。
proc_pgid() {
  [[ "$has_ps" == 1 ]] || return 0
  ps -o pgid= -p "$1" 2>/dev/null | tr -d ' ' || true
}

# 先尝试结束整个进程组，失败（如目标不是组长）再退回结束单个 PID。
# 绝不结束本脚本自己所在的进程组。
signal_process() {
  local pid="$1" sig="$2" pgid own_pgid
  [[ -n "$pid" ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  pgid="$(proc_pgid "$pid")"
  own_pgid="$(proc_pgid $$)"
  if [[ -n "$pgid" && "$pgid" != "1" && "$pgid" != "$own_pgid" ]]; then
    kill "-$sig" -- "-$pgid" 2>/dev/null && return 0
  fi
  kill "-$sig" "$pid" 2>/dev/null || true
}

# 只认本仓库的进程：工作目录在仓库内，或命令行里出现本仓库路径/后端可执行文件。
pid_belongs_to_project() {
  local pid="$1" cwd cmd
  cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1 || true)"
  case "$cwd" in
    "$root_dir"|"$root_dir"/*) return 0 ;;
  esac
  [[ "$has_ps" == 1 ]] || return 1
  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  case "$cmd" in
    *"$root_dir"*|*"$novelvideo_bin"*) return 0 ;;
  esac
  return 1
}

wait_port_free() {
  local port="$1"
  # 必须逐条声明：macOS 自带 bash 3.2 会先展开整条 local 的所有词，
  # 写成 `local timeout=... deadline=$((... timeout ...))` 会报 unbound variable。
  local timeout="${2:-15}"
  local deadline=$((SECONDS + timeout))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    [[ -z "$(port_listener_pids "$port")" ]] && return 0
    sleep 0.2
  done
  [[ -z "$(port_listener_pids "$port")" ]]
}

# 停止上一次运行的服务：先按 PID 文件，再按端口兜底；遇到非本仓库进程就放弃。
stop_existing_services() {
  local pid port foreign=""
  local stopped=0

  if [[ -f "$pid_file" ]]; then
    echo "发现上次启动记录 ${pid_file}，正在停止旧进程……"
    while IFS= read -r pid; do
      [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || continue
      kill -0 "$pid" 2>/dev/null || continue
      if ! pid_belongs_to_project "$pid"; then
        echo "  PID $pid 已不属于本仓库（PID 可能被复用），跳过。" >&2
        continue
      fi
      echo "  停止 PID $pid"
      signal_process "$pid" TERM
      stopped=1
    done < "$pid_file"
    rm -f "$pid_file"
  fi

  for port in "$api_port" "$frontend_port"; do
    for pid in $(port_listener_pids "$port"); do
      kill -0 "$pid" 2>/dev/null || continue
      if pid_belongs_to_project "$pid"; then
        echo "  端口 $port 被本仓库进程 PID $pid 占用，停止它"
        signal_process "$pid" TERM
        stopped=1
      else
        foreign="${foreign}"$'\n'"  - 端口 $port 被其他程序的 PID $pid 占用"
      fi
    done
  done

  if [[ -n "$foreign" ]]; then
    echo "检测到非本仓库进程占用端口，为避免误杀已中止：$foreign" >&2
    echo "请先手动处理这些进程，再运行本脚本。" >&2
    return 1
  fi

  for port in "$api_port" "$frontend_port"; do
    if ! wait_port_free "$port" "$stop_timeout"; then
      echo "端口 $port 在 ${stop_timeout}s 内未释放，强制结束。" >&2
      for pid in $(port_listener_pids "$port"); do
        signal_process "$pid" KILL
      done
      if ! wait_port_free "$port" 5; then
        echo "端口 $port 仍被占用，请手动检查。" >&2
        return 1
      fi
    fi
  done

  if [[ "$stopped" == 1 ]]; then
    echo "旧服务已停止。"
  fi
  return 0
}

case "${1:-}" in
  --help|-h)
    usage
    exit 0
    ;;
  ""|--check) ;;
  *)
    echo "未知参数: $1（使用 --help 查看用法）" >&2
    exit 2
    ;;
esac

if [[ ! -x "$novelvideo_bin" ]]; then
  echo "找不到可执行文件: ${novelvideo_bin}，请设置 NOVELVIDEO_BIN。" >&2
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
if [[ "$has_lsof" == 0 ]]; then
  echo "警告: 未找到 lsof，只能依赖 PID 文件停止旧服务，端口占用检测已禁用。" >&2
fi

printf '后端程序: %s\n数据目录: %s\n状态目录: %s\n' \
  "$novelvideo_bin" "$NOVELVIDEO_DATA_ROOT" "$NOVELVIDEO_STATE_DIR"

if [[ "${1:-}" == --check ]]; then
  echo "端口占用:"
  for port in "$api_port" "$frontend_port"; do
    pids="$(port_listener_pids "$port")"
    if [[ -z "$pids" ]]; then
      echo "  $port: 空闲"
      continue
    fi
    for pid in $pids; do
      if pid_belongs_to_project "$pid"; then
        echo "  $port: 本仓库进程 PID ${pid}（正式启动时会先停止它）"
      else
        echo "  $port: 其他程序 PID ${pid}（不会被自动停止）"
      fi
    done
  done
  exit 0
fi

stop_existing_services

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
      signal_process "$pid" TERM
    fi
  done
  # 只在 PID 文件仍属于本次启动时才删除：被别人接管后，这个文件已经记着
  # 新实例的 PID，删掉会让下一次重启只能靠端口兜底。
  if [[ -n "$api_pid" ]] && [[ -f "$pid_file" ]] && grep -qx "$api_pid" "$pid_file" 2>/dev/null; then
    rm -f "$pid_file"
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$root_dir"
"$novelvideo_bin" api --host 127.0.0.1 --port "$api_port" &
api_pid=$!
# 先记下 API PID：就绪等待期间被 Ctrl+C 或异常退出时，下次启动仍能定位到它。
mkdir -p "$(dirname "$pid_file")"
printf '%s\n' "$api_pid" > "$pid_file"

# API 冷启动要十几秒，而 Vite 只要几百毫秒。先起前端会让代理在 API 就绪前
# 刷出一串 `ECONNREFUSED 127.0.0.1:<api_port>`，首屏也跟着报错，所以这里
# 等 API 就绪后再拉前端。
if command -v curl >/dev/null 2>&1; then
  echo "等待 API 就绪……"
  api_ready_url="http://127.0.0.1:${api_port}/api/v1/config"
  api_ready_deadline=$((SECONDS + api_ready_timeout))
  until curl -fsS --max-time 2 "$api_ready_url" >/dev/null 2>&1; do
    if ! kill -0 "$api_pid" 2>/dev/null; then
      echo "API 进程在就绪前退出，请查看上方日志。" >&2
      exit 1
    fi
    if [[ "$SECONDS" -ge "$api_ready_deadline" ]]; then
      echo "API 在 ${api_ready_timeout}s 内未就绪: ${api_ready_url}" >&2
      exit 1
    fi
    sleep 1
  done
  echo "API 已就绪。"
  # /api/v1/config 返回 200 也不代表会话可用（与 scripts/start-ce.sh 同样的检查）。
  if [[ -z "${ST_LOCAL_API_TOKEN:-}" ]]; then
    auth_status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:${api_port}/api/v1/auth/me" 2>/dev/null || true)"
    if [[ "$auth_status" == "401" ]]; then
      echo "启动检查失败: GET /api/v1/auth/me 返回 401，前端会在 /login 之间反复跳转。" >&2
      echo "请检查 NOVELVIDEO_PUBLIC_HOST / ST_LOCAL_API_TOKEN 配置。" >&2
      exit 1
    fi
  fi
else
  echo "警告: 未找到 curl，跳过 API 就绪等待（前端可能先报代理连接失败）。" >&2
fi

(
  cd "$root_dir/frontend"
  exec pnpm dev --host 127.0.0.1 --port "$frontend_port" --strictPort
) &
frontend_pid=$!

mkdir -p "$(dirname "$pid_file")"
printf '%s\n%s\n' "$api_pid" "$frontend_pid" > "$pid_file"

echo "启动中，服务就绪后访问: http://127.0.0.1:${frontend_port}"
echo "API: http://127.0.0.1:${api_port}；按 Ctrl+C 同时停止前后端。"
echo "重启服务: 再次运行 bash scripts/start-local.sh（会先停止当前服务）。"
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
echo "有服务退出，请查看上方日志。" >&2
exit 1

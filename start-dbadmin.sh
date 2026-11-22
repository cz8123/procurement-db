#!/usr/bin/env bash
# ====================================================================
#  采购数据库管理页面 启动器 (bash 版)
#  适用：WorkBuddy Bash / Git Bash / 任意 POSIX shell (Windows)
#  用法：
#    bash start-dbadmin.sh                 # 前台运行（请用工具「后台模式」托管）
#    PORT=8080 bash start-dbadmin.sh       # 自定义端口
#    HOST=127.0.0.1 bash start-dbadmin.sh  # 仅本机访问
#  说明：脚本会先清理占用目标端口的旧 Flask 进程，再前台（exec）启动，
#        避免 Address already in use，也便于用后台任务方式托管。
# ====================================================================
set -u

# 自动定位 Python：项目 .venv -> WorkBuddy 用户 venv -> PATH
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=""
for cand in "$HERE/.venv/Scripts/python.exe" "$USERPROFILE/.workbuddy/binaries/python/envs/procurement/Scripts/python.exe"; do
  if [ -x "$cand" ] && "$cand" -c "import flask" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  for cand in $(where python 2>/dev/null | tr -d '\r'); do
    if "$cand" -c "import flask" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
APP="$HERE/app.py"
PORT="${PORT:-5000}"
HOST="${HOST:-0.0.0.0}"

if [ -z "$PY" ]; then
  echo "[错误] 未找到带 Flask 的 Python。请先执行：" >&2
  echo "        python -m venv .venv && .venv/Scripts/python.exe -m pip install flask openpyxl" >&2
  exit 1
fi
if [ ! -f "$APP" ]; then
  echo "[错误] 未找到 app.py: $APP" >&2
  exit 1
fi

# 清理占用目标端口的旧进程（避免端口冲突）
pids=$(netstat -ano 2>/dev/null | grep LISTENING | grep -E "[:.]${PORT}\b" | awk '{print $5}' | sort -u)
for pid in $pids; do
  [ -n "$pid" ] || continue
  echo "[清理] 终止占用 ${PORT} 端口的旧进程 PID=${pid}"
  taskkill //F //PID "$pid" >/dev/null 2>&1 || true
done
[ -n "$pids" ] && sleep 1

echo "正在启动采购数据库管理页面： http://${HOST}:${PORT}"
echo "（关闭本任务，或在页面点「终止服务」按钮即可停止）"
exec "$PY" "$APP"

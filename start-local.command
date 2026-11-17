#!/bin/sh
# Start the existing local workspace using its private, ignored configuration.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
if [ ! -f .env.local ]; then
  echo '缺少 .env.local，未启动；请先配置本地地址、依赖和原数据目录。' >&2
  exit 1
fi
set -a
. ./.env.local
set +a
: "${BIND:?}" "${PORT:?}" "${DATA_DIR:?}" "${PYTHON_BIN:?}" "${NODE_BIN:?}" "${FFMPEG_BIN:?}"
for binary in "$PYTHON_BIN" "$NODE_BIN" "$FFMPEG_BIN"; do
  if [ ! -x "$binary" ]; then
    echo "依赖不可用，未启动：$binary" >&2
    exit 1
  fi
done
if [ ! -f "$DATA_DIR/hub.sqlite" ]; then
  echo "原数据库不存在，未启动，避免误建空库：$DATA_DIR/hub.sqlite" >&2
  exit 1
fi
PATH="$(dirname -- "$NODE_BIN"):/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
echo "本地项目：http://$BIND:$PORT"
echo "沿用数据：$DATA_DIR"
case "${1:-}" in
  --check) exit 0 ;;
  '') ;;
  *) echo '用法：start-local.command [--check]' >&2; exit 2 ;;
esac
# Refuse a duplicate launch before app initialization touches the database.
"$PYTHON_BIN" - <<'PY'
import os, socket, sys
with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((os.environ['BIND'], int(os.environ['PORT'])))
    except OSError as exc:
        print(f'监听地址暂不可用，未启动：{exc}。若已有服务运行，请先正常停止。', file=sys.stderr)
        sys.exit(1)
PY
exec "$PYTHON_BIN" app.py

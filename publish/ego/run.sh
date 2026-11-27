#!/bin/sh
# 用法：sh publish/ego/run.sh read_stats [输出.csv]
#       sh publish/ego/run.sh fill_post dy|xhs <成品文件夹> <文案.txt> [定时 "YYYY-MM-DD HH:MM"]
# 把参数写成 ARGS 拼到脚本前面，交给 ego-browser nodejs（ego 脚本读不到 shell 参数和环境变量）。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
name="$1"; shift
ARGS=$(python3 -c 'import json,sys,os; print(json.dumps([os.path.abspath(a) if os.path.exists(a) else a for a in sys.argv[1:]], ensure_ascii=False))' "$@")
{ printf 'const ARGS = %s;\nconst HERE = %s;\n' "$ARGS" "\"$HERE\""; cat "$HERE/$name.mjs"; } | ego-browser nodejs

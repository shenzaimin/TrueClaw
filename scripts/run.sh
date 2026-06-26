#!/usr/bin/env bash
# 仓库内一键运行 trueclaw（无需 pip install，自动设置 PYTHONPATH）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "error: TrueClaw 需要 Python 3.11+（当前: $(python3 --version 2>&1))" >&2
  exit 1
fi

export PYTHONPATH="${ROOT}/src"
exec python3 -m trueclaw "$@"

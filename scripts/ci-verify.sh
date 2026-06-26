#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${ROOT}/src"
CONFIG="${CONFIG:-${ROOT}/trueclaw.local.json}"

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "error: TrueClaw 需要 Python 3.11+（当前: $(python3 --version 2>&1))" >&2
  exit 1
fi

echo "==> trueclaw ci-verify (config=${CONFIG})"

python3 -m trueclaw --version
python3 -m trueclaw --config "${CONFIG}" config validate
python3 -m trueclaw --config "${CONFIG}" doctor
python3 -m trueclaw --config "${CONFIG}" verify

echo "==> ci-verify PASS"

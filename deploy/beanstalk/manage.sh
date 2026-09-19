#!/bin/bash
set -euo pipefail
# This helper runs as webapp, never as root. The path is recorded by Gunicorn.
if [[ "$(id -u)" = 0 ]]; then
    echo 'Run this helper with sudo -u webapp, not as root.' >&2
    exit 1
fi
IFS= read -r python_bin < /var/lib/armangle/runtime-python
if [[ "$python_bin" != /var/app/venv/*/bin/python* ]] || [[ ! -x "$python_bin" ]]; then
    echo 'The deployed Python interpreter is unavailable; check deployment status.' >&2
    exit 1
fi
cd /var/app/current
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
exec "$python_bin" -m hosted.manage --data /var/lib/armangle "$@"

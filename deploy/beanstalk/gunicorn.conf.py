"""Elastic Beanstalk's nginx proxies to port 8000 on loopback."""
import os
from pathlib import Path
import sys

bind = '127.0.0.1:8000'
workers = 1
worker_class = 'gthread'
threads = 4
timeout = 150
graceful_timeout = 150
max_requests = 10000
max_requests_jitter = 500
accesslog = None
errorlog = '-'
capture_output = True
limit_request_line = 4096
# CloudFront's managed forwarding policy adds device/location/TLS headers.
limit_request_fields = 100
limit_request_field_size = 8190
umask = 0o077
raw_env = ['OPENBLAS_NUM_THREADS=1', 'OMP_NUM_THREADS=1', 'PYTHONDONTWRITEBYTECODE=1']


def on_starting(server):
    # EB virtualenv names vary by release; preserve the actual interpreter for
    # the cleanup timer and account-management command. No secrets are recorded.
    root = Path(os.environ.get('ARMANGLE_DATA', '/var/lib/armangle'))
    path = root / 'runtime-python'
    temporary = root / 'runtime-python.new'
    with temporary.open('w') as handle:
        handle.write(sys.executable + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)

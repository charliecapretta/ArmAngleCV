#!/bin/bash
set -euo pipefail
systemctl enable --now armangle-cleanup.timer
# Do not output environment variables or credentials into deployment logs.

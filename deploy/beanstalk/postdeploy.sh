#!/bin/bash
set -euo pipefail
systemctl enable --now armangle-cleanup.timer
if [[ -f /etc/armangle-backup.env ]]; then
    systemctl enable --now armangle-backup.timer armangle-backup-prune.timer
fi
if [[ -f /var/app/current/bundled-examples/manifest.json ]]; then
    sudo -u webapp /usr/local/libexec/armangle-manage examples-install /var/app/current/bundled-examples
fi
# Do not output environment variables or credentials into deployment logs.

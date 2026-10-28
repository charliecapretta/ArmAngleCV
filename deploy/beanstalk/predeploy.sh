#!/bin/bash
set -euo pipefail
# Runs as root in the EB staging application directory.
install -d -m 700 -o webapp -g webapp /var/lib/armangle
install -d -m 755 /usr/local/libexec /etc/systemd/system/web.service.d
install -m 755 deploy/beanstalk/manage.sh /usr/local/libexec/armangle-manage
install -m 644 deploy/beanstalk/cleanup.service /etc/systemd/system/armangle-cleanup.service
install -m 644 deploy/armangle-cleanup.timer /etc/systemd/system/armangle-cleanup.timer
install -m 644 deploy/beanstalk/backup.service /etc/systemd/system/armangle-backup.service
install -m 644 deploy/beanstalk/backup.timer /etc/systemd/system/armangle-backup.timer
install -m 644 deploy/beanstalk/backup-prune.service /etc/systemd/system/armangle-backup-prune.service
install -m 644 deploy/beanstalk/backup-prune.timer /etc/systemd/system/armangle-backup-prune.timer
install -m 644 deploy/beanstalk/web-limits.conf /etc/systemd/system/web.service.d/armangle.conf
systemctl daemon-reload
# AL2023 has no ffprobe package; install the vetted static build from the bundle.
if [ -f deploy/bin/ffprobe ]; then
    sha256sum --check --status deploy/bin/ffprobe.sha256 || { echo 'bundled ffprobe failed its checksum' >&2; exit 1; }
    install -m 755 deploy/bin/ffprobe /usr/local/bin/ffprobe
    /usr/local/bin/ffprobe -version >/dev/null 2>&1 || { echo "bundled ffprobe does not run on $(uname -m)" >&2; exit 1; }
fi
# Fail deployment clearly rather than admitting uploads with unverified duration.
command -v ffprobe >/dev/null || { echo 'ffprobe is required for upload duration validation' >&2; exit 1; }

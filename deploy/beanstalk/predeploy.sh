#!/bin/bash
set -euo pipefail
# Runs as root in the EB staging application directory.
install -d -m 700 -o webapp -g webapp /var/lib/armangle
install -d -m 755 /usr/local/libexec /etc/systemd/system/web.service.d
install -m 755 deploy/beanstalk/manage.sh /usr/local/libexec/armangle-manage
install -m 644 deploy/beanstalk/cleanup.service /etc/systemd/system/armangle-cleanup.service
install -m 644 deploy/armangle-cleanup.timer /etc/systemd/system/armangle-cleanup.timer
install -m 644 deploy/beanstalk/web-limits.conf /etc/systemd/system/web.service.d/armangle.conf
systemctl daemon-reload

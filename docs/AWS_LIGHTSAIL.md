# Arm Angle CV: $7 Lightsail private beta

The hosted entry point is `hosted.app:create_app()`. The existing `python -m armangle`
command remains the local, single-user app. Do not expose the local server to the internet.
The hosted app uses the same frozen manual-v1 model and shared point validation.

## Capacity and scope

Target: Ubuntu 24.04 LTS, Linux x86_64, Lightsail public-IPv4 **1 GB RAM / $7 per month**
instance. Confirm the displayed price and region before creating it. This is the instance
price, not a guaranteed total bill: domain registration, snapshots/backups, transfer
beyond the bundle, taxes and unused static IPs can add costs. No AWS resource has been created. See `docs/HOSTED_TEST_REVIEW.md` for the latest
verification results and remaining deployment checks.

Start with 3–5 invited friends, with one person uploading at a time. Limit uploads to
MP4, 200 MB, 60 seconds, 1920 × 1080, 60 fps, 3,600 frames. Decoding happens in a
separate process with a 100-second timeout. Only one upload decoder runs at a time;
other upload attempts receive an explicit busy/retry response. JPEG frames are stored
on private disk and served one at a time. No whole-video server RAM cache is used.
One clip may use up to 800 MB of prepared frames in addition to its original upload.

Accounts have 1 GB storage, the app has a 5 GB global quota, and uploads reserve the
declared source size plus 800 MB for frames (up to 1,000 MB) before beginning. This can reject a clip even when some storage remains.
Keep at least 1 GB of free disk space. Limit each session to 50 pitch annotations.
Videos, prepared frames, and annotations expire together after 7 days. Expired sessions
are inaccessible immediately; the hourly cleanup removes them from disk. Export CSVs
before expiry. Deleting a session removes its live data immediately. Backups are retained
for at most another 7 days. The account screen allows early session deletion and logout.

SQLite transactions persist annotations and undo across restarts. Opaque session/video
IDs do not grant access: every lookup checks the signed-in owner. Original paths and raw
uploads have no public route. Training, local-path imports, pose/ball detection, static
project-directory serving, and public registration are absent from the hosted API.

The deployment runs one Gunicorn process and four request threads. Do not increase worker
count on this plan. Native numerical libraries are restricted to one thread. systemd
limits the complete service, including its video subprocess, to 700 MB; exceeding that
limit may restart the service and interrupt an upload. This is a containment limit, not
a capacity guarantee. Memory and latency must be measured again on the actual instance
before inviting testers. AWS CPU performance differs from the development machine.

## 1. Prepare and check locally

From the project folder with Python 3.12:

```sh
.venv/bin/python -m pip install -r requirements-hosted.txt
.venv/bin/python -m unittest discover -s tests -v
node --test tests/labelTool.test.cjs
.venv/bin/python tools/checkRelease.py
.venv/bin/python tools/prepareHosted.py --out release/armangle-hosted.zip
```

Choose a new archive name for later builds. The packager includes only the explicit runtime
allowlist; personal videos, annotations, source datasets, `.git`, and secrets are excluded.
Keep deployment secrets outside this archive. Never publish this working tree's Git history.

## 2. Set up AWS and HTTPS

1. Secure the AWS account with MFA, set a billing budget and alerts, and inspect the account's
   credit/free-tier terms. Alerts notify; they do not impose a hard spending cap.
2. Create the **$7 Linux / 1 GB / public IPv4** Lightsail instance using Ubuntu 24.04 LTS.
3. Attach a Lightsail static IP. Allow inbound TCP 80 and 443; restrict SSH port 22 to your
   own IP. Do not open ports 8777 or 8778. Configure IPv6 firewall rules too if IPv6 is enabled.
4. Point a hostname you control at that static IP. Caddy will obtain/renew HTTPS for it.
   A free hostname you control can work; a purchased domain is optional. Keep DNS access.
5. Install Python 3.12, venv support, unzip, and OpenCV's Linux runtime libraries:

```sh
sudo apt-get update
sudo apt-get install -y python3.12-venv unzip libgl1 libglib2.0-0
```

Install Caddy using its official Debian/Ubuntu instructions:
https://caddyserver.com/docs/install#debian-ubuntu-raspbian

## 3. Install the runtime

Upload the prepared ZIP to the instance over SSH/SCP (or your preferred authenticated file
transfer). Create a dedicated OS user and app directory:

```sh
sudo useradd --system --home /var/lib/armangle --shell /usr/sbin/nologin armangle
sudo install -d -o root -g root /opt/armangle
sudo install -d -m 700 -o armangle -g armangle /var/lib/armangle
sudo unzip armangle-hosted.zip -d /opt/armangle
sudo python3.12 -m venv /opt/armangle/.venv
sudo /opt/armangle/.venv/bin/pip install --no-cache-dir -r /opt/armangle/requirements-hosted.txt
```

Copy `deploy/armangle.env.example` to `/etc/armangle.env`, set mode **600**, owned by root,
and edit it with `sudoedit`. Set `ARMANGLE_ORIGIN` to your exact HTTPS origin (no path),
keep the data directory at `/var/lib/armangle`, and generate `ARMANGLE_SECRET` with:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Keep this secret in a password manager for recovery; do not send it in chat or commit it.
Copy `deploy/Caddyfile` to `/etc/caddy/Caddyfile` and replace `YOUR_DOMAIN` with your hostname.

```sh
sudo cp /opt/armangle/deploy/armangle.service /etc/systemd/system/
sudo cp /opt/armangle/deploy/armangle-cleanup.service /etc/systemd/system/
sudo cp /opt/armangle/deploy/armangle-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now armangle armangle-cleanup.timer
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The application binds only to loopback. Caddy is the sole public entry point and supplies
trusted proxy headers. Never run this ProxyFix-configured app directly on a public address.

## 4. Create private accounts

Run from `/opt/armangle`:

```sh
sudo -u armangle .venv/bin/python -m hosted.manage user friendname
```

The command prompts for a password of at least 12 characters without echoing it. Give each
friend their own username and password privately. Share the HTTPS link. There is no shared
account, public sign-up or email service. Password recovery is handled by the owner rerunning
this command; it invalidates existing sessions. Revoke access with:

```sh
sudo -u armangle .venv/bin/python -m hosted.manage revoke friendname
```

## Optional: the practice clip

The first-run page offers "Try an MLB example" only when a practice clip is installed.
Without one, a visitor with no MP4 of their own has nothing to open, so the tile stays
hidden. Install a clip by copying an MP4 to the instance and running:

```sh
sudo -u armangle .venv/bin/python -m hosted.manage example install /tmp/clip.mp4 \
  --pitcher 'Scherzer, Max' --throws R --park TOR --angle 38.2
```

`--meta pitch.json` reads the same fields from a `uiTestBatch.json` entry instead, and
`--slug` names the clip; without it the name is `mlb-example`. Install several, under
different slugs, to cover a range of arm slots.

Each clip is prepared once into `/var/lib/armangle/examples/`, outside the per-visitor
`videos/` tree, and the seven-day cleanup never removes it. A visitor who opens the
example draws one installed clip at random and keeps it: reopening returns the pitch
they have already worked on rather than drawing again. Their copy is a pitch of their
own whose frames are hard links to the shared one, so annotations, exports and deletions
stay private, the clone costs no additional disk and no storage quota, and retention
expires the clone rather than the clip. `example list` and `example remove --slug` show
and withdraw them; `example install --replace` rebuilds one in place. Budget about 65 MB
of prepared frames per clip.

`--angle` is the Statcast arm angle. The review step reveals it once all five points are
placed, so the practice run ends in a comparison against a known answer. Uploads have no
such reference and still show none.

Serving a clip here publishes it to every visitor, which is not what the downloader in
`scripts/fetchUiTestBatch.py` retrieves footage for. Confirm you may redistribute a clip before
installing it, and remove it with `example remove --slug` if that changes.

## 5. Verify before inviting friends

- Sign in as two different users. Upload disposable clips; neither should see the other's
  sessions or be able to retrieve frames, predict, edit, delete, undo, or export their data.
- Choose a release, mark five points, save three pitches, reload, and export the CSV.
- Upload a maximum-size supported clip; measure preparation time and memory. While it is
  preparing, have the other account browse saved frames. Confirm a second upload returns busy.
- Check the service memory and errors with `systemctl status armangle`,
  `systemctl show armangle -p MemoryCurrent -p MemoryPeak`, and `journalctl -u armangle`.
  Aim to remain comfortably below the 700 MB limit. Reduce upload limits if necessary.
- Restart with `sudo systemctl restart armangle`; confirm saved work and exports remain.
- Test delete, expiry cleanup, account revocation, and backup/restore before real uploads.
- If TLS, Linux dependency installation or capacity checks fail, keep the beta closed.

## 6. Back up and recover

The database and video folders are durable on the instance's disk, but losing the instance
or disk also loses them. Before accepting real uploads, create a consistent backup and
copy it off the instance to private storage. An off-instance destination is deliberately not
provisioned by the app: choose one within your budget. Backups contain private videos and
password hashes. Do not put them in a public bucket or GitHub repository.

Create a backup outside the runtime data directory:

```sh
sudo install -d -m 700 -o armangle -g armangle /var/backups/armangle
cd /opt/armangle
sudo -u armangle .venv/bin/python -m hosted.manage backup /var/backups/armangle/backup-YYYY-MM-DD.tar.gz
```

Use a new filename each time. Copy it off-instance, retain backups for **7 days maximum**,
and schedule this daily only after choosing the destination and confirming its cost. The
backup command locks against upload/deletion and uses SQLite's online backup API. Labels
can continue to save; the snapshot captures one consistent point in time.

Restore into a **new** data directory while the service is stopped:

```sh
sudo systemctl stop armangle armangle-cleanup.timer
sudo install -d -m 700 -o armangle -g armangle /var/lib/armangle-recovery
sudo -u armangle .venv/bin/python -m hosted.manage --data /var/lib/armangle-recovery/restored restore /path/to/backup.tar.gz
```

The backup file must be readable by the service user. Update
`ARMANGLE_DATA` and both systemd units' `ReadWritePaths` to the restored directory, keep the
original data until verified, reload systemd, then start the app and cleanup timer. Restore
the deployment secret separately. Test sign-in, a frame, and CSV contents. Run cleanup to
remove sessions that expired while the backup was stored.

## Updates and eventual public sharing

Deploy a newly prepared archive with the service stopped; replace application files only,
never `/var/lib/armangle`. Keep the previous package for rollback. SQLite schema changes
need an explicit migration before future releases. This version has no destructive migrations.

The same HTTPS URL can later be posted publicly, but it will still require an invited account.
Open public registration and anonymous uploads are intentionally disabled. Before changing
that, measure real usage, add an abuse-control/capacity plan, decide retention and support,
and verify the budget. The $7 instance is a small beta target, not a promise of public-scale
capacity. Hiding the training tab alone does not provide hosted security.

References (checked September 2026):
- https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html
- https://flask.palletsprojects.com/en/stable/deploying/gunicorn/
- https://aws.amazon.com/aws-cost-management/aws-budgets/faqs/
- https://caddyserver.com/docs/quick-starts/https

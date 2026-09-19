# Elastic Beanstalk — Python 3.12 browser-session beta

The current Beanstalk bundle sets `ARMANGLE_ACCESS=anonymous`: visitors open the
analysis page directly without a username or password. A signed, Secure, HttpOnly
browser cookie separates each visitor's uploads and annotations. Returning in the
same browser retains access; clearing cookies, switching devices, or letting the
cookie expire loses access. There is no recovery account. Export CSVs to keep results.
Videos and annotations still expire after seven days. No visitor database row is
created until a successful upload; orphan guest rows are removed during cleanup.

Anonymous uploads allow 10 preparation attempts per browser per hour and 30 per
client IP per hour (including invalid media). Changing cookies does not bypass the
IP budget. The 500 MB per-browser and 5 GB global storage ceilings and single decoder
remain enforced. Browser limits are not a durable per-person quota: visitors can clear
cookies. Verify trusted client-IP forwarding before public sharing; otherwise users
may share one proxy's limit. Existing account records remain separate and inaccessible
to anonymous visitors. Anyone with the link can use the anonymous deployment.

No-sign-in mode still requires HTTPS, the server secret, CSRF checks, disabled CDN
caching and a correctly configured proxy. CloudFront viewer HTTPS alone does not
secure its connection to an HTTP Beanstalk origin. The package does not provision
CloudFront or secure that origin connection. Finish that setup before sharing uploads.

Use the **Python 3.12 on Amazon Linux 2023** web-server platform and a **single-instance**
environment for this disk-backed beta. This is a separate package from the Lightsail ZIP.
Beanstalk itself has no additional service fee, but underlying EC2, EBS, IPv4, load-balancer,
S3 and other resources can incur charges. Confirm your account's actual free-plan/credit
eligibility; the word "free" does not make every configuration free.

## Before uploading

The application requires a working HTTPS origin. The default `*.elasticbeanstalk.com`
address does not by itself establish that HTTPS has been configured. Do not use HTTP for
passwords or uploaded private footage, and do not disable the application's Secure cookies
or HTTPS origin checks to bypass this step.

Set these environment properties in the Beanstalk console (Configuration → environment
properties, typically under Updates, monitoring, and logging; wording can vary):

| Property | Value |
|---|---|
| `ARMANGLE_ORIGIN` | Exact working HTTPS origin, for example `https://analysis.example.com`, with no path |
| `ARMANGLE_SECRET` | At least 32 random characters, generated locally; retain securely for recovery |
| `ARMANGLE_ACCESS` | `anonymous` (bundle default); use `accounts` only if sign-in is desired |
| `ARMANGLE_PROXY` | `cloudfront-vpc` for the restricted private CloudFront origin described below; otherwise `single` |

Generate the secret with `python3 -c 'import secrets; print(secrets.token_hex(32))'` and paste
it into AWS yourself. Do not put it in a ZIP, source code, Git, screenshots or chat. Environment
settings can contain sensitive values; do not post full deployment configuration/log dumps.
The bundle sets `ARMANGLE_DATA=/var/lib/armangle` and single-thread numerical-library limits.
Do not change this data path without updating its platform hooks and cleanup unit too.

A single-instance environment can terminate HTTPS in its nginx proxy using a certificate
for a hostname you control, with inbound 443 enabled. A load balancer with ACM is another
option but adds infrastructure/cost; this package does not provision either. Configure
certificate renewal and forwarded HTTPS headers as part of that setup. Keep the public
HTTP listener redirecting to the HTTPS hostname except the health check route. Check the
actual proxy chain before inviting testers: the app expects nginx to supply a trustworthy
`X-Forwarded-Proto` and client-address header; it must not accept arbitrary client spoofing.
If using a load balancer, restrict instance access to that load balancer and verify nginx
preserves its HTTPS scheme header. Do not expose Gunicorn port 8000 directly.

### CloudFront VPC origin without a purchased domain

Use the AWS-provided `https://…cloudfront.net` URL as `ARMANGLE_ORIGIN`. This route
uses viewer HTTPS and CloudFront's private VPC connection to the instance on HTTP
port 80; it does not claim TLS encryption on that internal HTTP hop.

1. Create a CloudFront VPC origin referencing the running Beanstalk EC2 instance ARN.
   Add it to the distribution using the instance's private DNS, then point the default
   behavior to that origin. Leave the origin path blank. Set response timeout to 120s.
2. On the instance security group, allow HTTP port 80 only from the AWS-managed
   `CloudFront-VPCOrigins-Service-SG`. Remove public HTTP sources and inspect every
   attached group for other public web ingress. Do not edit AWS's managed group.
   Keep outbound connectivity for Beanstalk deployment and monitoring; do not remove
   subnet internet routes without planning the required private endpoints/NAT.
3. Verify the sample page works through CloudFront and direct public HTTP access fails.
4. In the default behavior, use Redirect HTTP to HTTPS, all seven HTTP methods,
   `CachingDisabled`, and **`AllViewerAndCloudFrontHeaders-2022-06`** as origin request
   policy (replace `AllViewer`). Do not cache OPTIONS. The policy forwards cookies,
   query parameters, the viewer Host, and CloudFront-generated protocol/client-IP headers.
5. Set `ARMANGLE_PROXY=cloudfront-vpc`. The app ignores arbitrary X-Forwarded-* values
   in this mode and uses CloudFront-Forwarded-Proto and CloudFront-Viewer-Address.
   Missing/invalid headers or HTTP viewers receive 403; a mismatched public Host receives
   400. `/healthz` stays available for local health checks without those headers.

**The security-group restriction is required before enabling this proxy mode.** The
headers are trusted only because the origin accepts traffic from CloudFront's VPC
service and Gunicorn accepts only the local nginx proxy. Do not use this mode on a
publicly reachable origin. An environment secret is still required for guest cookies.

This VPC origin targets a specific EC2 instance. If Beanstalk replaces the instance,
update the VPC origin/distribution to the replacement and recheck security rules; the
old ARN will not automatically follow Beanstalk. Recheck CloudFront and direct public
access after every deployment/configuration update. This beta retains the local-disk
data-loss limitations below.

References:
- https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-vpc-origins.html
- https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html

## Upload and deploy

1. Choose **Upload local file** in Beanstalk.
2. Select the ZIP generated by `tools/prepareBeanstalk.py` (not the Lightsail package).
3. Use a new version label, for example `armangle-beta-20260919`.
4. Deploy after the environment properties and HTTPS route above are configured.
5. Check Beanstalk Events and health. Do not invite friends until opening the workspace,
   upload, save, reload and CSV export succeed through the real HTTPS address. Test two
   separate browsers to confirm their uploads stay separate.

The ZIP has `application.py`, `Procfile`, `requirements.txt`, `gunicorn.conf.py`, `.platform`
and `.ebextensions` at its root, with no enclosing project folder. Python dependencies include
Flask, Gunicorn and headless OpenCV. The entry point is `application:application` on loopback
port 8000, one worker and four request threads. The nginx extension permits 50 MiB uploads
and longer receive/processing timeouts. The platform hooks create the private data directory,
install hourly expiry cleanup, and apply memory limits to Beanstalk's `web.service`.
Do not manually install the Lightsail Caddy or `armangle.service` on this environment.

## Optional accounts and administration

Skip account creation in the default anonymous mode. Visitors can delete their own
videos from **My uploads**. To require individual sign-ins instead, set
`ARMANGLE_ACCESS=accounts` in Beanstalk environment properties, then follow the account
creation steps below. Changing modes separates the workspaces; it does not transfer
existing uploads between guests and accounts. If an older environment already has an
explicit `ARMANGLE_ACCESS` setting, update it to `anonymous` for no-sign-in access.

After deployment, connect to the instance using your configured SSH/Session Manager access.
The platform installs a helper using the deployed Python interpreter:

```sh
sudo -u webapp /usr/local/libexec/armangle-manage user yourname
sudo -u webapp /usr/local/libexec/armangle-manage user friendname
```

Passwords are prompted without echo. Share each tester's credentials privately. Revoke an
account with `sudo -u webapp /usr/local/libexec/armangle-manage revoke friendname`.
The app has no automatic shared/demo account and no public registration.

## The practice clip

The first-run page offers "Try an MLB example" only when a practice clip is installed.
Without one, a visitor with no MP4 of their own has nothing to open, so the tile stays
hidden. Install a clip by copying an MP4 to the instance and running:

```sh
sudo -u webapp /usr/local/libexec/armangle-manage example install /tmp/clip.mp4 \
  --pitcher 'Scherzer, Max' --throws R --park TOR --angle 38.2
```

`--meta pitch.json` reads the same fields from a `uiTestBatch.json` entry instead. The
clip is prepared once into `/var/lib/armangle/examples/`, outside the per-visitor
`videos/` tree, and the seven-day cleanup never removes it. Each visitor who opens the
example gets a pitch of their own whose frames are hard links to that one copy: their
annotations, exports and deletions stay private, the clone costs no additional disk and
no storage quota, and retention expires the clone rather than the clip. `example list`
and `example remove` show and withdraw it; `example install --replace` swaps in another.

`--angle` is the Statcast arm angle. The review step reveals it once all five points are
placed, so the practice run ends in a comparison against a known answer. Uploads have no
such reference and still show none.

Serving a clip here publishes it to every visitor, which is not what the downloader in
`fetchUiTestBatch.py` retrieves footage for. Confirm you may redistribute a clip before
installing it, and remove it with `example remove` if that changes.

Check health and expiry:

```sh
curl -fsS http://127.0.0.1:8000/healthz
sudo systemctl status web.service armangle-cleanup.timer
sudo systemctl show web.service -p MemoryCurrent -p MemoryPeak
sudo journalctl -u web.service -n 50
```

Gunicorn writes only its interpreter path to `/var/lib/armangle/runtime-python` for the
helper. No credentials are written there. Platform hook scripts must run successfully;
missing hooks can cause data-directory permissions or cleanup failures. The real Linux hooks,
nginx, systemd and certificates must be verified on AWS; local tests do not emulate them.

## Data and capacity limitations

This beta stores the database, originals and frames on **this instance's disk**, outside the
application release directory. They survive process restarts and ordinary in-place app
updates, but **instance replacement, environment rebuilding/termination, immutable or
blue/green deployment, and scaling out can lose or split that data**. Use one instance and
in-place/all-at-once deployments for disposable testing only until external durable storage
is added. These settings do not prevent AWS from replacing an unhealthy instance.
Optional private S3 original-video storage is implemented; see [S3 setup](S3_SETUP.md).
Frames and SQLite still live on the instance. S3 originals alone do not preserve your
annotations, browser ownership records, or a ready-to-use workspace after replacement.

Before non-disposable use, configure off-instance backups or migrate uploads and annotations
to durable external storage. Backing up videos alone does not preserve SQLite annotations.
For an instance-local backup to copy off the server privately:

```sh
sudo install -d -m 700 -o webapp -g webapp /var/backups/armangle
sudo -u webapp /usr/local/libexec/armangle-manage backup /var/backups/armangle/backup-YYYY-MM-DD.tar.gz
```

Use new filenames. Backups include private data; retain at most 7 days. Test restoration into
a new directory before relying on backups. Never put backup files in the application ZIP.

Upload limits: 50 MB, 30 seconds, 1080p, 60 fps, 1,800 frames; one preparation at a time. Per-browser storage
is 500 MB including JPEGs and S3 originals; global video storage is 5 GB. Keep at least
1 GB of free disk. Each upload reserves 450 MB for up to 50 MB of video plus 400 MB of
prepared JPEGs; complex clips can hit the JPEG cap and need trimming. Frames retain
the uploaded resolution and frame rate. Decoder deadline remains 90 seconds.
Videos and annotations expire together after 7 days. Service memory limit: 700 MB. Measure
on your actual instance before inviting 3–5 friends; a credit-eligible small instance is not
a guarantee of capacity for public traffic. Anonymous mode allows anyone with the link
to upload, including anyone a tester forwards it to. Confirm capacity and off-instance
backups before advertising it widely.

## Rebuild and verify

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_hosted*.py' -v
.venv/bin/python tools/prepareBeanstalk.py --out release/armangle-beanstalk-python312.zip
```

Choose a new ZIP filename if one already exists. Tests run extracted bundles with Gunicorn,
exercise the authenticated upload-to-CSV workflow, restart the process and verify isolation.
They simulate proxy headers and do not prove AWS DNS, TLS, IAM, memory or hooks work.

Official references:
- https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/python-configuration-procfile.html
- https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-linux-extend.hooks.html
- https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/platforms-linux-extend.proxy.html
- https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/configuring-https.html
- https://aws.amazon.com/elasticbeanstalk/pricing/

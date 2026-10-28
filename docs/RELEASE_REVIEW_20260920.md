# Release readiness review

Review completed September 20, 2026, using the September 19 working tree and
release candidate. Nothing was deployed or publicly published. Pre-existing
workspace changes were preserved; the candidate includes the current working
tree, not just changes made during this review.

## Completed

- Added an offline upload pause/status/resume command. Pauses survive application
  restarts; existing saved work remains usable. Browser notice and 503 responses
  explain the pause. An upload already in progress may finish.
- Added a public privacy/data page, configurable support email, and HTTPS HSTS.
- Added `ARMANGLE_EXAMPLES=off` to hide practice entry and block access to existing
  practice frames until permissions are settled. Saved CSV export remains available.
- Fixed backups of shared practice clips: tar now expands hard links so the
  strict restore validator can recover a two-user practice workspace.
- Added an encrypted S3 backup command, remote metadata verification, and optional
  daily systemd scheduler files. These are locally tested with a fake S3 service;
  no real bucket, IAM policy, scheduler or off-instance backup was configured.
- Updated synthetic capacity measurement to 60 seconds at 1080p/60fps. The full
  benchmark was deferred because of insufficient disk space.
- Added CI auditing of the generated deployment requirements.
- Built and checksum-verified a 44-file Beanstalk candidate without bundled MLB
  videos or user CSVs. The existing instance may still contain previously installed
  examples; a code-only deployment does not remove those files.
- Built the separate allowlisted public-source package. Its scanner passed and
  no Git history was copied. This does not establish rights to source datasets.

## Verification evidence

Evidence is in `release/verification-public-20260919/`.

| Check | Result |
|---|---|
| Hosted test suite after changes | 35/35 passed in its focused run |
| Final complete Python suite | 77/82 passed; five disk-related failures |
| JavaScript tests | 36/36 passed |
| Model provenance, refit, grouped score, resizing | Passed; model unchanged |
| Exact Beanstalk requirements vulnerability audit | 25 dependencies, zero known vulnerabilities, none skipped |
| ZIP manifest/file checksum verification | Passed |
| Public source allowlist/pattern scan | Passed |
| Upload pause/status/resume CLI | Passed on disposable data |
| Privacy page browser rendering | Verified at narrow viewport |
| Git whitespace check | Passed |

The final full suite is **not green**. Four packaged Gunicorn upload tests returned
413 because the real disk reserve could not be satisfied. The 200 MiB boundary
test encountered an actual `ENOSPC` while writing its disposable upload; that test
had passed in the focused hosted run. Free at least 3 GB (preferably 5 GB) before
rerunning. Do not reduce production limits to make these tests pass. Unit fixtures
model capacity; the new low-disk rejection test explicitly verifies the reserve.

An initial sandboxed run could not bind localhost; subsequent HTTP runs were
performed outside that sandbox and reached the real storage checks. The local
research environment also has an unrelated MediaPipe/protobuf `pip check`
conflict; MediaPipe is excluded from hosted requirements. A clean Linux CI run
remains a release gate. No CI run was triggered in this review.

The advisory audit is a point-in-time known-vulnerability check, not a guarantee
that every dependency or the application is vulnerability-free.

Candidate: `release/armangle-public-candidate-20260919.zip`

SHA-256: `8b710ae267742c60266ba72bf33017b12448f77e9e6b4d0325ef74e667f69d7b`

## Needed from the owner

1. Free disk space for the remaining local checks.
2. Supply the current HTTPS URL and a signed-in AWS session or configured local
   AWS access. No AWS CLI or signed-in browser session was available. Never send
   passwords or access keys in chat.
3. Set a maximum monthly AWS budget and a public support/privacy email.
4. Confirm permission to serve the MLB clips, or keep examples disabled and use
   footage that can be served publicly.

## Still required before public release

- Durable data storage for an unrestricted release, or an explicitly limited beta
  with agreed recovery loss and verified off-instance backups.
- Private backup bucket, least-privilege role, retention, scheduler, monitoring,
  and a downloaded snapshot restored onto a fresh instance.
- Verify the published privacy language against actual log/backup retention.
  S3 lifecycle expiry is asynchronous; a strict seven-day backup deletion promise
  needs a verified deletion process or adjusted wording before publication.
- Live HTTPS/origin isolation/cache/IAM review, edge throttling, cost alerts and
  operational monitoring.
- AWS capacity test with real clips and simultaneous users; live two-browser
  upload-to-export/deletion checks, cleanup, instance replacement and rollback.
- Resolve all local test failures, complete a clean Linux run, and record the
  deployed version only after these gates pass.

Use `docs/PUBLIC_RELEASE.md` for commands, configuration and recovery procedures.

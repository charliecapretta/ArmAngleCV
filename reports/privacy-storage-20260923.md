# Privacy and storage verification — September 23, 2026

Application changes and deployment requirements are documented in
[Privacy and storage](../docs/PRIVACY_STORAGE.md). Existing workspace edits were
preserved. No deployment, AWS configuration change, or user-data migration occurred.

## Verification

- Full Python discovery: 99 tests; 94 passed in the sandbox. The remaining five
  could not bind loopback ports (four packaged-server checks and local first run).
  All five passed when rerun with permission to bind temporary localhost ports.
- Eight new privacy regression tests passed, including a final rerun after the
  multipart parser adjustment. They cover original removal, frame recovery,
  deletion before restore, restore quarantine, remote ledger failure and bucket
  mismatch, owner isolation, delete-all CSRF/session clearing, file limits,
  public configuration and retention bounds.
- All 38 browser workflow tests passed.
- Real nginx limits test passed: throttling, 180 requests at 60fps without a 429,
  and oversized declared upload rejection before body transmission.
- Beanstalk and allowlisted public-source packages built successfully. All
  Beanstalk manifest hashes and new privacy files were verified. Shell syntax
  and git whitespace checks passed.

Some older tests leave response file handles open and emit ResourceWarnings.
Intentional simulated S3 failures emit error logs; assertions passed.
Linux systemd execution and live AWS behavior were not tested on this Mac.

Beanstalk candidate: `release/armangle-privacy-storage-20260923.zip`

SHA-256: `d26b3f8ce75499f78b6861b734b3b1fc47efda4859e371a9ef8ee489abeebcd9`

## Remaining release work

Configure the real support contact and independent deletion ledger, private
backup storage and IAM; enable and monitor backup/prune/cleanup jobs; verify
provider logging, edge caching, origin isolation and disk encryption. Perform
an isolated downloaded-backup recovery drill and real upload/capacity test.
Instance-local SQLite and frames remain a durability limitation. Keep a limited
beta until durable shared storage is implemented or an explicit recovery-loss
policy with verified off-instance snapshots is accepted.

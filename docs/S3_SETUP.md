# Private S3 originals for the Beanstalk beta

Prepared locally; requires a real bucket and instance-role permissions before use.
This is server-mediated upload, not presigned direct browser upload. The existing
file picker and progress bar work unchanged. S3 is optional: without the two settings
below, uploads keep their original file locally as before.

## What moves to S3

New uploads are received temporarily on the instance, decoded/validated, and stored
under `armangle/originals/<random-video-id>/source.mp4`. Only after S3 and SQLite
registration succeed is success returned to the browser. The local original is then
removed. No user filename, pitcher name, public ACL, or credentials are sent in S3
object metadata. SSE-S3 encryption and a SHA-256 metadata digest are set on each PUT.
The application never sends bucket names or S3 keys to browsers.

JPEG frames, metadata, SQLite ownership and annotations remain local. The same
signed browser-cookie ownership checks protect frames, edits, predictions and exports.
Existing local videos are not migrated automatically. Original objects alone cannot
restore a workspace if Beanstalk replaces the instance; local database/frame backups
are still required. This is deliberately a small storage change, not a multi-instance
or persistent-database migration.

Supported uploads: MP4, up to 50 MB, 30 seconds, 1920 × 1080, 60 fps and 1,800 frames.
The decoder preserves resolution/frame rate, writes frames sequentially, and stops at
90 seconds or 400 MB of JPEGs. The app reserves 450 MB before processing, still enforces
500 MB per browser / 5 GB global stored-video quotas, and leaves 1 GB free locally.
On a 1 GB instance, test real clips before broad sharing; S3 does not offload decoding.

## 1. Create a dedicated bucket

In S3, choose **Create bucket**:

- Region: **US East (Ohio), us-east-2**, matching Beanstalk.
- Type: **General purpose**.
- Name: a unique name, for example `armanglecv-videos-<your-unique-suffix>`.
- Object Ownership: **Bucket owner enforced / ACLs disabled**.
- **Block all public access: ON**.
- Bucket versioning: **Disabled** (the initial state of a new bucket).
- Default encryption: **SSE-S3** (Amazon S3 managed keys).
- Object Lock: **Off**.
- Do not enable website hosting or add public-read policies.

Use a new dedicated non-versioned bucket. Enabled or suspended versioning is rejected
by the app to avoid retaining hidden copies after retries/deletion. Do not change the
bucket to versioned while this integration is in use. No CORS rule is needed because
the browser communicates only with the application.

Under **Management → Create lifecycle rule**, create an enabled rule named
`expire-armangle-originals` filtered to prefix `armangle/originals/`, expiring current
objects after **7 days**. This is a fallback for abandoned uploads or instance loss;
S3 lifecycle deletion is asynchronous. Do not apply it to unrelated objects.

## 2. Give the EC2 instance role narrow access

The instance uses `aws-elasticbeanstalk-ec2-role` in the current environment. Confirm
that role on **EC2 → instance → Security → IAM role**. Add the following inline policy
to the **instance role**, not the Beanstalk service role. Replace `YOUR_BUCKET_NAME`
in both resource strings. No AWS access keys should be added to the app or browser.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CheckDedicatedBucketVersioning",
      "Effect": "Allow",
      "Action": "s3:GetBucketVersioning",
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME"
    },
    {
      "Sid": "StoreAndExpireArmAngleOriginals",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET_NAME/armangle/originals/*"
    }
  ]
}
```

`DeleteObjectVersion` allows cleanup of an exact returned version if bucket settings
change during a request. It does not make versioned buckets supported. Account-level
policies, permission boundaries and bucket policies must also permit these actions.

## 3. Configure and deploy

Add these Beanstalk environment properties (plain text; they are not credentials):

| Property | Value |
|---|---|
| `ARMANGLE_S3_BUCKET` | The bucket's name only, not `s3://` or a URL |
| `ARMANGLE_S3_REGION` | `us-east-2` |

Keep `ARMANGLE_SECRET`, `ARMANGLE_ORIGIN`, `ARMANGLE_ACCESS=anonymous` and
`ARMANGLE_PROXY=cloudfront-vpc`. Upload the newly generated 1080p/S3 bundle after the
bucket permissions and lifecycle rule are ready. No S3 credentials go in the ZIP.
If S3 is misconfigured or unavailable, upload returns 503 and creates no visible
session; it does not silently fall back to local-only storage.

## Deletion, failures and recovery

Each prospective S3 object is recorded durably in SQLite before PUT. A successful
registration marks it active in the same transaction as the video/pitch. Failed or
interrupted uploads leave a pending/deletion record for cleanup. Deleting a video
immediately removes access, annotations and local frames, then attempts S3 deletion.
If S3 deletion fails, the object stays queued and the hourly cleanup retries it.
Expiry similarly removes local data and queues/deletes the original after seven days.
The independent S3 lifecycle rule covers loss of the instance and its cleanup queue.

The cleanup helper reads bucket/key/region from SQLite and uses the instance role,
so it does not need access to the web service's secret/environment properties.
Watch `journalctl -u armangle-cleanup.service` for deferred deletions.

Backups include local frames and S3 references, not another copy of the S3 originals.
Restore validates those references but does not contact S3 or revive expired objects.
Restore only with the original live environment stopped; this remains a single-instance
application. Old local-only backups remain supported. Do not share backup archives.

## Verification after deployment

1. Open the CloudFront link and upload a short, non-private 1080p/60fps test MP4.
2. Confirm all frames are available, mark points, save, reload and export CSV.
3. In S3, verify one encrypted original under the prefix; confirm Block Public Access.
4. Open a separate browser session and verify it cannot see the upload.
5. Delete the test session from My uploads; verify the original disappears from S3.
6. Confirm cleanup timer and instance memory with the Beanstalk guide's commands.

S3 request/storage charges are separate from EC2/Beanstalk. No new AWS resources or
policies are provisioned by the bundle; the bucket/role setup is performed in the console.

References:
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/intro-lifecycle-rules.html
- https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html
- https://docs.aws.amazon.com/boto3/latest/guide/credentials.html

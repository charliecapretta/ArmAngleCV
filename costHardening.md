# Cost and abuse hardening verification

Local results, September 21, 2026. No deployment or AWS account changes were made.

## Measurement

`tools/measureSession.py` starts a real Flask HTTP server on loopback with disposable data. It installs five real clips selected across the bundled demo arm-angle range, opens an anonymous browser-cookie session, walks every frame, loads one full-size release per pitch, predicts and saves five-point annotations, exports each CSV, and conditionally reloads every requested frame. Payload bytes exclude HTTP/TLS overhead. This is an HTTP workload, not browser automation; the UI's resolution selection and coordinates are tested separately.

| Metric | JPEG/no-cache baseline | Hardened |
| --- | ---: | ---: |
| Five-pitch first pass | 374,535,952 bytes (374.54 MB) | 57,104,045 bytes (57.10 MB) |
| First pass plus complete reload | 748,943,366 bytes | 57,104,045 bytes |
| Average requested frame | 194,497 bytes | 29,597 bytes |
| Frame requests on first pass | 1,925 | 1,925 |
| Total requests, including reload | 3,876 | 3,876 |
| Full-body frame downloads on reload | 1,925 | **0** |
| Frame 304s on reload | 0 | **1,925** |

First-pass payload fell **6.56× (84.8%)**; including a reload, **13.12×**. All normal session requests succeeded. Browser freshness caching can eliminate the conditional reload requests too; the script deliberately revalidates to verify 304 handling.

The local five clips contain 1,920 frames, not the 1,810 in the supplied live measurement, so the local baseline is larger. Use the identical local clips for before/after comparisons. The baseline was captured before changing the encoder/UI, with the original JPEG q85/no-store response behavior restored in the harness after initial budget work began. `--baseline` retains that encoding/cache behavior for reproducibility.

Commands (Python 3.12, hosted requirements, ffprobe and nginx installed):

```sh
.venv/bin/python tools/measureSession.py --baseline --output /tmp/before.json
.venv/bin/python tools/measureSession.py --output /tmp/after.json
.venv/bin/python tools/checkCostLimits.py
.venv/bin/python -m unittest discover -s tests -p 'testCostHardening.py' -q
.venv/bin/python -m unittest discover -s tests -p 'test_hosted.py' -q
.venv/bin/python -m unittest discover -s tests -p 'test_hosted_http.py' -q
node --test tests/labelTool.test.cjs
```

Raw measurements: [before](reports/costHardening/before.json), [after](reports/costHardening/after.json), [nginx bursts](reports/costHardening/nginxLimits.json).

## Limits and rationale

| Endpoint/budget | Limit | Reason |
| --- | --- | --- |
| nginx frames, per viewer IP | 90 requests/sec, burst 90 | Allows 60fps playback plus initial previews; still bounds burst concurrency |
| nginx upload, per viewer IP | 2 requests/min, burst 2 | Preparation is expensive and serialized |
| nginx predict, per viewer IP | 2 requests/sec, burst 6 | Manual five-point editing needs far fewer predictions |
| nginx other API routes, per viewer IP | 10 requests/sec, burst 20 | Permits page setup/save traffic while limiting floods |
| SQLite daily frames | 10,000/session; 20,000/IP | Five complete passes over five 362-frame demos use 9,050 requests; IP budget resists cookie rotation |
| SQLite daily upload attempts | 10/session; 30/IP | Limits decoding work, including malformed videos; independent of deletion and cookie rotation |
| Upload body and duration | 200 MiB; 60 seconds | Existing advertised limits retained; ffprobe now rejects duration before OpenCV opens the decoder |

Daily budgets reset at midnight UTC, persist through restarts, and reserve atomically across workers. Rejected daily requests return JSON and seconds until reset in `Retry-After`; nginx uses 60 seconds. Existing hourly anonymous upload limits, storage quotas and preparation deadlines remain.

Real nginx burst tests produced 118/240 frame 429s, 9/12 upload 429s, 23/30 prediction 429s, and 58/80 general API 429s. Counts can vary with elapsed time; every 429 carried JSON and `Retry-After: 60`. A separate 180-request stream at 60fps had **zero 429s**. nginx rejected an oversized declared body with 413 before the test sent any body bytes. Flask independently rejects oversize uploads before starting the decoder. A real 61-second MP4 was rejected by ffprobe before any `VideoCapture` call.

These controls bound protected frame requests and upload preparation per cookie/IP, not total AWS spend or a distributed attacker with many IPs. See [infrastructure handoff](infraHandoff.md).

## Precision and compatibility

Full frames retain original dimensions and use WebP q78, encoder method 1. Scrub previews use at most 640px width, q60, method 2. No full-size neighbor prefetch occurs in point mode; thumbnails always use previews. Canvas views and stored coordinates remain in original pixels; only the bitmap source rectangle is scaled. Normal and zoomed coordinate equivalence tests pass, including serialization through `buildRow`.

**The sub-60 KB average is across the labeling workload, not full-resolution images alone.** Across all 1,920 generated full-resolution frames, the average was 122.68 KB; the five requested locked frames averaged 129.42 KB. Forcing these below 60 KB would require more compression or a resolution reduction. This implementation preserves joint detail and obtains the savings during browsing instead. It meets the session-average and total-transfer acceptance thresholds, but not a separate interpretation requiring full-resolution frames alone to average under 60 KB.

The [3× comparison](reports/costHardening/precisionComparison.png) shows decoded source at left and final q78 at right. Visual inspection found comparable shoulder, elbow and wrist outlines; texture softens and the source's motion blur remains. This is a visual sample check, not a clinical/subpixel accuracy study. Preview resolution reduces browsing detail; precise placement waits for the full frame. The preview and full frame use the same source frame index and aspect ratio.

Legacy JPEG videos remain readable. Bundle installation upgrades shared templates while existing visitors retain their immutable JPEG hardlinks until expiry. Backup/restore validates both formats and includes scrub variants. No annotation schema, CSV schema or model artifact changed.

## Hard constraints checked individually

1. **Session gate:** frame requests without cookies return 401, including conditional requests. Another user's cookie gets 404. No frame is public or edge-cacheable.
2. **Security headers:** exact existing CSP retained (including `default-src 'self'`, no `unsafe-eval`), plus unchanged DENY, nosniff and same-origin headers. Tests check exact values.
3. **CSRF:** write requests with missing tokens return 403 without touching an instrumented unreadable body. Existing token and Origin checks remain.
4. **Example idempotency:** existing tests verify repeated calls reuse all five pitches and preserve an existing single example. Template-upgrade tests verify old private frame bytes survive unchanged.
5. **Seven-day lifecycle:** unchanged retention and account copy; existing tests verify expiry, deletion, S3 cleanup and shared-template survival. Conditional frame access after deletion returns 404. As requested, private browser caching lasts seven days after download; deleting server data cannot erase an already downloaded browser copy.
6. **Naming:** new helpers/files use camelCase; framework overrides and conventional Flask configuration keys retain their required API spellings.

Verification passed: **35 hosted tests, 7 new hardening tests, 4 packaged Gunicorn HTTP tests, 38 UI tests, and 2 release-packaging tests**, plus real nginx checks. Public static assets also omit `Set-Cookie`, preventing Flask's automatic session refresh from leaking a cookie into an edge-cacheable response.

Install the updated hosted requirements and provision ffprobe in the service PATH before deployment. The Beanstalk hook fails clearly if it is missing. CI now installs ffmpeg/nginx and runs the real proxy checks. Re-measure the live distribution after deployment; this task measured locally only.

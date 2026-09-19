# V1 hosted launch checklist

Status: stages 2–3 implemented locally on September 14, 2026; verification results below. No cloud deployment has been performed. The owner confirmed $100 Azure student credit.

## Goal and starting decisions

A user opens a website, uploads a short pitching video, selects and locks a release frame for each pitch, marks five landmarks, saves the pitches, and downloads a CSV of model estimates.

- Start with an invite-only beta for 3–5 testers on desktop browsers.
- Evaluate Azure first because of the Student Developer Pack. Azure is a proposed provider, pending account eligibility and an actual cost estimate.
- Keep the existing frozen `manual-v1` model and manual labeling workflow.
- College accuracy cannot be verified with the available reference data. College validation is not a launch gate. Repeatability checks do not establish accuracy.
- Treat upload limits and retention below as proposed beta defaults, to be adjusted after measurement.

Execute the stages in order. Software work in stages 2–5 can proceed while account verification is pending. Owners: **You** = account/product decisions; **Implementation** = project work.

## 1. Confirm the hosting account and budget — You + Implementation

- [x] Azure for Students credit confirmed by owner: $100 available. No further redemption needed.
- [ ] Record the subscription's actual available credit, expiration, eligible services, and region availability.
- [ ] Set a proposed operating budget of $35/month before credits. Price Azure compute, storage, networking, and backups together; the earlier Render estimate is not an Azure quote.
- [ ] Configure cost alerts and inspect the subscription spending limit. Budget alerts are notifications, not a guaranteed shutoff. Retain the student spending limit where available.
- [ ] Choose a provider-issued HTTPS address for the beta; a purchased domain can wait.

**Done when:** the account is usable and a resource configuration has a documented expected cost and credit runway. Choose the final machine size after stage 5's memory measurement.

## 2. Finish the user workflow locally — Implementation

- [x] Make Pitch analysis the default entry page (ready for the later hosted build), with a clear Upload → Release → Points → Review → Export flow.
- [x] Replace pasted server file paths with a file picker and actual upload endpoint; show upload/preparation progress and useful errors.
- [x] Start by accepting supported MP4 clips. Proposed caps: 100 MB, 60 seconds, and 1080p; verify frame rate and decoded-frame limits as well as file size.
- [x] Let users name a session and optionally enter pitcher name and throwing hand.
- [x] Lock a release frame before labeling throwing shoulder, opposite shoulder, elbow, wrist, and ball. Changing the frame invalidates its old points and prediction.
- [x] Provide zoom, point adjustment, ball-unavailable status, and release/point quality notes.
- [x] Support several pitches from one upload, with a visible saved-pitch list and edit/resume controls.
- [x] Add a session CSV download, using saved annotations as its source. Ensure edits cannot export stale predictions.

**Done when:** one person can upload a clip, tag three pitches, reload, edit a pitch, and export matching results without a terminal.

## 3. Define estimates and CSV behavior — Implementation

- [x] Show measured image angle and predicted arm angle as separate values, in degrees.
- [x] Use this wording in Review, help, and export metadata: “Estimated arm angle from an MLB-trained model. Accuracy on college footage has not been established.”
- [x] Keep the 2.653° development cross-validation result in model documentation with its exact scope; do not present it as college accuracy or a per-pitch confidence interval.
- [x] Export one row per saved pitch, with incomplete/occluded pitches retained under an explicit status and a blank prediction.
- [x] Include `session_id`, `pitch_id`, `pitcher_name`, `video_name`, `release_frame`, `release_time_sec`, `measured_angle_deg`, `predicted_arm_angle_deg`, `status`, `release_quality`, `point_quality`, `model_version`, and `estimate_scope`.
- [x] Document frame numbering and angle conventions; omit server paths from exports and treat user-entered text safely when generating spreadsheet-ready CSVs.
- [x] Match hosted predictions against the existing `angleInputs.py` / `predictLabels.py` path on fixed saved inputs. Do not retrain as part of deployment.

**Done when:** exports identify each pitch, reproduce its saved estimate, and preserve the limits of the result.

### Stages 2–3 verification — September 14, 2026

- 43 Python tests and 18 JavaScript tests passed. The real HTTP test uploads an MP4, creates three pitch records, saves/reopens annotations, compares session CSV with the existing CLI, and checks edits, undo, release changes, invalid uploads, and preservation of training labels.
- Browser-tested the file chooser, named session, locked release, point placement, ball-unavailable save, three-pitch reload, editing a saved point, and a successful CSV download event. All browser writes used disposable preview data; the test markings are not accuracy evidence.
- Verified frozen model/data checksums, refit identity, grouped development score, and resize invariance. No retraining.
- Verified the release allowlist includes the new modules and CSV documentation and excludes uploaded videos. The app remains local; stage 4 is still required before internet exposure.

## 4. Make the backend suitable for multiple users — Implementation

- [ ] Replace the local `SimpleHTTPRequestHandler` server with a production web application and server, reusing labeling and prediction functions.
- [ ] Serve an explicit set of frontend assets. The current project-directory fallback must not expose source files, models, CSVs, registrations, or uploads.
- [ ] Remove server-path import and disable training, automatic pose, and ball-detection endpoints in the hosted build.
- [ ] Add individual authenticated access for invited testers and enforce ownership on every video, frame, label, prediction, export, delete, and undo request. A shared password alone does not isolate users.
- [ ] Validate uploaded content and decoded limits, generate internal filenames, and constrain temporary storage, processing time, and concurrent decoding.
- [ ] Replace the unbounded whole-clip memory behavior with a measured, bounded frame cache or on-demand decoding. Start with one video-preparation job at a time and a visible waiting state.
- [ ] Store sessions/annotations in a durable database and videos in private durable storage, separately from application code. Use transactional saves and request-scoped undo.
- [ ] Proposed retention: remove original videos after 7 days; retain annotations for 30 days. Show expiry before upload, allow early deletion, and explain when frame review will stop being available.
- [ ] Make cleanup work after restarts and apply it to temporary files, frames, videos, and expired sessions. Define backup retention and verify a restore.

**Done when:** two users can work independently; neither can retrieve or alter the other's records; saved work survives a process restart.

## 5. Package and verify a deployable build — Implementation

- [ ] Package the manual app, frozen model, and required dependencies in a reproducible Linux build. Use Python 3.12 unless compatibility is deliberately revalidated.
- [ ] Add production startup configuration, environment-based settings, a lightweight health endpoint, and useful error logs without exposing upload contents or credentials.
- [ ] Create the cloud deployment source from reviewed files. Follow `docs/RELEASE.md`; do not publish the existing private Git history. Review/update the release allowlist for the hosted app.
- [ ] Run existing Python/JavaScript checks and model release verification against disposable data.
- [ ] Add hosted-flow checks: upload validation, user isolation, save/reload, stale prediction prevention, CSV contents, expiry/deletion, and restart recovery.
- [ ] Measure peak memory and preparation time with the maximum accepted clip and two active labeling sessions. Reduce limits or change cache behavior if the proposed server cannot handle it.
- [ ] Record the tested capacity and finalize the Azure resource estimate from stage 1.

**Done when:** a clean build passes the workflow and isolation checks, and the chosen server has measured capacity for the beta.

## 6. Deploy a restricted beta — You + Implementation

- [ ] Review the concrete resource list, region, monthly estimate, and deployment contents before provisioning billable resources.
- [ ] Create a dedicated Azure resource group. Proposed starting service: Linux App Service for the Python app, with private durable storage and a small database selected within the budget.
- [ ] Configure production secrets, HTTPS, individual beta access, health monitoring, and storage access permissions.
- [ ] Deploy the verified build to the provider-issued address. Record the source revision and model checksum.
- [ ] Verify the full upload-to-CSV flow remotely in Chrome and Safari using disposable videos/accounts.
- [ ] Restart/redeploy and confirm saved work remains. Test restoring data and rolling back to the previous build before inviting testers.

**Done when:** the restricted live URL works, deployment is repeatable, and recovery has been exercised.

## 7. Run the first user trial — You + testers

- [ ] Invite 3–5 testers with a short walkthrough and an explanation of upload retention and unverified college estimates.
- [ ] Have each tester upload a short clip, label at least three pitches, reopen the session, and export CSV without developer intervention.
- [ ] Collect failures, confusing steps, processing time, memory usage, and actual cloud spend.
- [ ] Check repeated labeling on the same fixed frames to assess usability and consistency. Do not describe this as college accuracy validation.
- [ ] Fix blocking issues and repeat only the affected checks.

**Done when:** testers complete the core flow, data stays isolated and recoverable, and costs remain within the agreed budget.

## 8. Release V1 — You + Implementation

- [ ] Tag the tested application release `v1.0.0`, preserving the separate model identifier `manual-v1`.
- [ ] Publish the entry URL, short instructions, example CSV, supported clip limits, retention rules, model limitations, and a feedback channel.
- [ ] Decide whether access stays invitation-only or opens more broadly after reviewing beta capacity and spend.
- [ ] Record how to deploy updates, restore backups, roll back, remove user data, and disable/remove cloud resources when credits expire.

**Launch acceptance:** a new user can open the hosted app, upload supported footage, tag multiple releases and landmarks, save/reopen work, and download correct per-pitch CSV estimates. College ground-truth verification is not required and is not claimed.

## Provider references

Offers and service availability must be rechecked when provisioning.

- [GitHub Student Developer Pack](https://education.github.com/pack)
- [Azure for Students: credit and eligibility](https://azure.microsoft.com/en-us/free/students)
- [Deploy a Python app to Azure App Service](https://learn.microsoft.com/en-us/azure/app-service/quickstart-python)
- [Configure Python startup on Azure App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-language-python)
- [Azure spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit)

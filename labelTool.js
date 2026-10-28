"use strict";

const WORKSPACE = document.documentElement?.dataset.workspace === "analysis" ? "analysis" : "training";
const ANALYSIS = WORKSPACE === "analysis";
const HOSTED = document.documentElement?.dataset.hosted === "true";
const HAS_EXAMPLE = document.documentElement?.dataset.example === "true";
const csrfToken = () => HOSTED ? document.querySelector('meta[name="csrf-token"]')?.content || "" : "";

const state = {
  pitches: [], sessionFilter: "", exporting: false, index: 0, current: null, labels: new Map(), history: [], drafts: new Map(),
  frame: 0, img: null, imgCache: new Map(), points: {}, origin: {}, mode: "release",
  zoom: false, zoomView: null, pan: null, zoomHinted: false,
  drag: null, active: null, placingPoint: false, ballOccluded: false,
  loading: false, busy: false, poseBusy: false, playing: false, playToken: 0,
  reviewing: false, predictedAngle: null, predictionBusy: false,
  releaseQuality: "unreviewed", pointQuality: "unreviewed", predictionToken: 0,
  viewToken: 0, imageToken: 0, poseToken: 0, pickingPitcher: false, dirty: false
};
const POINTS = ["shoulder", "offShoulder", "elbow", "wrist", "ball"];
const POSE_POINTS = POINTS.slice(0, 4);
const POINT_COLOR = { shoulder:"#ffd166", offShoulder:"#f78c6b", elbow:"#ef476f", wrist:"#06d6a0", ball:"#ff6ec7" };
const POINT_LABEL = { shoulder:"throwing shoulder", offShoulder:"glove side shoulder", elbow:"elbow", wrist:"wrist", ball:"ball" };
const PREFIX = { shoulder:"shoulder", offShoulder:"off_shoulder", elbow:"elbow", wrist:"wrist", ball:"ball" };
const el = id => document.getElementById(id);
const canvas = el("stage"), ctx = canvas.getContext("2d");
const keyOf = pitch => `${pitch.gamePk}_${pitch.playId}`;
const blocked = () => state.busy || state.loading;
// Uploads have no reference angle; the practice clip does, and comparing
// against it is the reason to offer one. Training always sees the reference.
const showsReference = () => !ANALYSIS || !!state.current?.example || !!state.current?.uiTestBatch;
// On the analysis MLB clips the published angle is the number worth reading
// first, so it takes the headline card and the measured angle moves to the
// secondary row. Uploads have no reference, and training must not headline the
// truth while points are still being judged, so both keep the original order.
const referenceLeads = () => ANALYSIS && (!!state.current?.example || !!state.current?.uiTestBatch);
const hasValue = value => value !== "" && value != null && Number.isFinite(Number(value));
const hasPoint = (row, name) => hasValue(row?.[`${PREFIX[name]}_x`]) && hasValue(row?.[`${PREFIX[name]}_y`]);
function isComplete(row) {
  return !!row && (row.status === "skipped" ||
    (row.status === "occluded" && POSE_POINTS.every(n => hasPoint(row, n))) ||
    (row.status === "labeled" && POINTS.every(n => hasPoint(row, n))));
}
function needsTopUp(row) { return row?.status === "labeled" && !isComplete(row); }
function toast(message) {
  el("toast").textContent = message; el("toast").classList.add("show");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => el("toast").classList.remove("show"), 2600);
}
function showError(message = "") { el("error").textContent = message; el("error").hidden = !message; }
async function request(url, body) {
  if (["/api/pitches", "/api/labels"].includes(url)) url += `?workspace=${WORKSPACE}`;
  if (body !== undefined) body = {...body, workspace:WORKSPACE};
  const response = await fetch(url, body === undefined ? {} : {
    method:"POST", headers:{"Content-Type":"application/json", "X-CSRF-Token":csrfToken()}, body:JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok || result.error || (body !== undefined && result.ok !== true))
    throw new Error(result.error || "Request failed. Please try again.");
  return result;
}
function clearPoints() {
  state.pointQuality = "unreviewed"; clearPrediction();
  state.points = {}; state.origin = {}; state.active = null; state.drag = null;
  state.placingPoint = false; state.ballOccluded = false; state.pickingPitcher = false;
  state.zoomView = null; state.pan = null;
  el("seedWarn").hidden = true;
}
function nextEmptyPoint() {
  return POINTS.find(n => !state.points[n] && !(n === "ball" && state.ballOccluded)) || null;
}
function restorePoints(row) {
  for (const name of POINTS) if (hasPoint(row, name)) {
    state.points[name] = {x:Number(row[`${PREFIX[name]}_x`]), y:Number(row[`${PREFIX[name]}_y`])};
    state.origin[name] = "saved";
  }
  state.ballOccluded = row.status === "occluded";
  state.active = nextEmptyPoint();
}
function timeAt(frame) {
  const recorded = state.current?.frameTimes?.[frame];
  return recorded != null ? recorded : (state.current?.fps > 0 ? frame / state.current.fps : null);
}
function initialFrame(pitch, saved) {
  if (hasValue(saved?.frame_index)) return Number(saved.frame_index);
  if (pitch.isLocal) return pitch.startFrame || 0;
  if (ANALYSIS || pitch.example || pitch.uiTestBatch) return 180;
  // Compare seconds, not raw frame indices from different frame rates.
  const seconds = state.pitches.filter(p => !p.isLocal).flatMap(p => {
    const row = state.labels.get(keyOf(p));
    if (!row || row.status === "skipped" || !hasValue(row.frame_index)) return [];
    const fps = Number(row.fps) || p.fps;
    return hasValue(row.release_time_sec) ? [Number(row.release_time_sec)] : fps > 0 ? [Number(row.frame_index) / fps] : [];
  }).sort((a,b) => a-b);
  return seconds.length >= 3 && pitch.fps > 0 ? Math.round(seconds[Math.floor(seconds.length/2)] * pitch.fps) : Math.floor(pitch.nFrames/2);
}
function stashDraft() {
  if (!state.current || !state.dirty) return;
  state.drafts.set(keyOf(state.current), JSON.parse(JSON.stringify({
    frame:state.frame, points:state.points, origin:state.origin, mode:state.mode,
    ballOccluded:state.ballOccluded, reviewing:state.reviewing, releaseQuality:state.releaseQuality, pointQuality:state.pointQuality, dirty:true
  })));
}
function clearPrediction() { state.predictionToken++; state.predictedAngle = null; state.predictionBusy = false; el("anglePrediction").textContent = "—"; }
function markDirty() { clearPrediction(); state.dirty = true; el("saveState").textContent = "Unsaved changes"; el("exportStatus").textContent = ""; }
function visiblePitches() {
  return state.pitches.filter(p => {
    const row = state.labels.get(keyOf(p)), filter = el("queueFilter").value;
    if (ANALYSIS && state.sessionFilter && p.sessionId !== state.sessionFilter) return false;
    if (filter === "batch") return p.uiTestBatch;
    if (filter === "local") return p.isLocal;
    if (filter === "release") return !row;
    if (filter === "points") return row?.status === "release_selected" || needsTopUp(row);
    return true;
  });
}
function renderQueue() {
  el("pitchSelect").replaceChildren();
  const visible = visiblePitches();
  const pitches = state.current && !visible.includes(state.current) ? [state.current, ...visible] : visible;
  for (const p of pitches) {
    const row = state.labels.get(keyOf(p)), option = document.createElement("option");
    const status = row?.status === "release_selected" ? "needs points" : isComplete(row) ? "finished" : "needs release";
    option.value = keyOf(p);
    option.textContent = `${p.pitcherName || "Local pitch"} · ${p.park || "Unknown park"} · ${status} · #${state.pitches.indexOf(p)+1}`;
    option.selected = p === state.current; el("pitchSelect").append(option);
  }
}
function nextAnalysisPitch() {
  const order = [...state.pitches.slice(state.index+1), ...state.pitches.slice(0,state.index)];
  const queue = visiblePitches();
  return order.find(p => queue.includes(p) && !isComplete(state.labels.get(keyOf(p))));
}
async function continueAnalysis() {
  if (blocked() || state.dirty) return;
  const next = nextAnalysisPitch();
  if (next) await showPitch(state.pitches.indexOf(next));
}
function renderWorkflow() {
  const points = state.mode === "points", review = points && state.reviewing, locked = blocked();
  const savedReview = ANALYSIS && review && !state.dirty && isComplete(state.labels.get(keyOf(state.current || {})));
  el("releaseStep").className = !points ? "active" : "done";
  el("pointsStep").className = review ? "done" : points ? "active" : "";
  el("reviewStep").className = review ? "active" : "";
  el("stepNumber").textContent = ANALYSIS ? `STEP ${review ? 4 : points ? 3 : 2} OF 5` : `STEP ${review ? 3 : points ? 2 : 1} OF 3`;
  if (ANALYSIS) {
    el("releaseStep").textContent = "2 Release"; el("pointsStep").textContent = "3 Points"; el("reviewStep").textContent = "4 Review";
    el("exportStep").className = savedReview ? "active" : "";
    if (savedReview) { el("reviewStep").className = "done"; el("stepNumber").textContent = "STEP 5 OF 5"; }
  }
  el("stepTitle").textContent = review ? (ANALYSIS ? "Your angle estimate" : "Review & save") : points ? "Mark five points" : "Choose the release";
  el("stepHelp").textContent = review
    ? (ANALYSIS ? "Review the estimate and your marked frame. Save when you’re ready." : "Check the points on the image, then save this pitch to the training dataset.")
    : points ? "Click each landmark in order. Select a name to replace a point; once all points are placed, drag to adjust."
    : "Use the timeline and neighboring frames to choose the closest available release. An exact, clean frame is not required.";
  if (savedReview) {
    el("stepTitle").textContent = "Pitch saved";
    el("stepHelp").textContent = state.current?.example || state.current?.uiTestBatch
      ? "Continue to the next example, or export this pitch as CSV."
      : "Export this session as CSV, or add another pitch from this video.";
  }
  el("releaseActions").hidden = points; el("pointActions").hidden = !points || review;
  el("pointButtons").hidden = !points || review; el("occludedLabel").hidden = !points || review;
  el("reviewActions").hidden = !review;
  el("measurePanel").hidden = !points; el("thumbs").hidden = points;
  el("ballOccluded").checked = state.ballOccluded;
  el("releaseQuality").value = state.releaseQuality; el("pointQuality").value = state.pointQuality;
  el("pointQualityLabel").hidden = !points;
  el("releaseQuality").disabled = el("pointQuality").disabled = locked || state.poseBusy;
  el("releaseNext").hidden = points || ANALYSIS;
  el("seedPoints").hidden = !points || review || ANALYSIS;
  el("choosePitcher").hidden = !points || review || ANALYSIS;
  for (const id of ["prevPitch","nextPitch","pitchSelect","queueFilter","openImport","skipPitch","undoSave","newLocalPitch","editPoints"])
    el(id).disabled = locked;
  for (const id of ["confirmRelease","releaseNext","savePoints","seedPoints","choosePitcher","changeRelease","zoomPoints","ballOccluded"])
    el(id).disabled = locked || state.poseBusy || !state.img;
  el("reviewPoints").disabled = locked || state.poseBusy || !(state.ballOccluded ? POSE_POINTS : POINTS).every(n => state.points[n]);
  el("scrub").disabled = locked || points;
  el("prevFrame").disabled = el("nextFrame").disabled = locked || points;
  el("playPause").disabled = state.busy || points || (state.loading && !state.playing);
  el("playSpeed").disabled = locked || points;
  el("playPause").textContent = state.playing ? "Pause" : "Play";
  el("undoSave").disabled = locked || !state.history.length;
  el("newLocalPitch").hidden = !ANALYSIS || !state.current?.isLocal || !!state.current?.example;
  el("newLocalPitch").disabled = locked || state.dirty || !state.labels.has(keyOf(state.current || {}));
  el("truthDetails").hidden = !showsReference() || !review;
  if (!showsReference() || !review) { el("sanityWarn").hidden = true; el("pDelta").textContent = "—"; }
  el("resultCard").hidden = !ANALYSIS;
  el("predictAngle").hidden = !ANALYSIS;
  el("savePoints").textContent = ANALYSIS ? "Save analysis" : "Save & next pitch →";
  el("savePoints").hidden = savedReview;
  const next = savedReview ? nextAnalysisPitch() : null;
  el("nextReviewPitch").hidden = !savedReview || !next;
  el("nextReviewPitch").disabled = locked;
  el("nextReviewPitch").textContent = `Next pitch →${next?.pitcherName ? ` ${next.pitcherName}` : ""}`;
  el("reviewSaved").hidden = !savedReview;
  el("reviewSaved").textContent = next ? "Saved. Ready for the next pitch." : "Saved. All pitches in this queue are complete.";
  const practice = !!state.current?.example || !!state.current?.uiTestBatch;
  if (savedReview && !next && practice) el("stepHelp").textContent = "All examples in this queue are complete. Export this pitch as CSV, or choose another pitch to review.";
  el("workspaceBadge").textContent = ANALYSIS ? (practice ? "MLB PRACTICE" : "YOUR FOOTAGE") : "DATASET WORKSPACE";
  const exportLabel = practice ? "Export pitch CSV" : "Export session CSV";
  el("exportSession").textContent = el("downloadResult").textContent = exportLabel;
  el("confirmRelease").textContent = ANALYSIS ? "Use this release frame →" : "Use this frame →";
  el("practiceComparison").hidden = !ANALYSIS || !practice || !review;
  if (ANALYSIS && review && !state.ballOccluded && (state.predictionBusy || state.predictedAngle == null)) el("savePoints").disabled = true;
  el("predictAngle").disabled = locked || state.poseBusy || state.predictionBusy || !points || state.ballOccluded || !POINTS.every(n => state.points[n]);
  el("downloadResult").disabled = !canExportSession();
  el("exportSession").disabled = !canExportSession();
  el("csvFallback").hidden = !canExportSession() || el("csvFallback").dataset?.session !== selectedSessionId();
  el("sessionSelect").disabled = locked;
  el("closeImport").disabled = state.busy;
  for (const button of el("sessionPitches").children) button.disabled = locked;
  if (state.current) {
    el("downloadRelease").href = frameUrl(state.frame) + "&download=1";
    const time = timeAt(state.frame);
    el("frameNum").textContent = `${time == null ? "time unknown" : time.toFixed(3)+" s"} · frame ${state.frame} / ${state.current.nFrames-1}${points ? " · locked" : ""}`;
    el("viewerTitle").textContent = state.current.pitcherName || state.current.sourceName || "Pitch preview";
  }
  el("zoomPoints").textContent = state.zoom ? "Full frame (Z)" : "Zoom to pitcher (Z)";
  el("viewerHint").textContent = review ? "Review the marked frame. Choose Adjust points to make changes."
    : points ? (state.zoom
        ? "Zoomed in · scroll to zoom · shift-drag to pan · Z for the full frame"
        : "The release frame is locked. Place all points on this same image · scroll to zoom")
    : "← / → step one frame · Space plays slowly · Click a thumbnail to compare.";
  renderPointButtons();
  if (review) renderReview();
}

const MEASURED_LABEL = "MEASURED SHOULDER → BALL";
const MEASURED_NOTE = "From your marked points, relative to image horizontal. Camera perspective affects this measurement.";
const MEASURED_MISSING_NOTE = "Mark the ball to measure this angle.";
const REFERENCE_LABEL = "STATCAST REFERENCE";
const REFERENCE_NOTE = "Statcast's published arm angle for this pitch, revealed only after placement. Compare it with your measured angle.";

/**
 * Fills the headline card and the secondary row. Which number goes where is
 * `referenceLeads()`: MLB clips lead with the Statcast angle, everything else
 * leads with what the marked points measure.
 */
function renderAngleCards() {
  const measured = state.ballOccluded ? null : pixelAngle("ball");
  const measuredText = measured == null ? "Unavailable" : `${measured.toFixed(1)}°`;
  const measuredNote = measured == null ? MEASURED_MISSING_NOTE : MEASURED_NOTE;
  const truth = state.current?.armAngle;
  const truthText = truth == null ? "Unavailable" : `${Number(truth).toFixed(1)}°`;
  const lead = referenceLeads();
  el("reviewBallLabel").textContent = lead ? REFERENCE_LABEL : MEASURED_LABEL;
  el("reviewBallAngle").textContent = lead ? truthText : measuredText;
  el("reviewBallNote").textContent = lead ? REFERENCE_NOTE : measuredNote;
  el("pTruthLabel").textContent = lead ? "Measured shoulder → ball" : "Statcast reference";
  el("pTruth").textContent = lead ? measuredText : truthText;
  el("pTruthNote").textContent = lead ? measuredNote : REFERENCE_NOTE;
}
function renderReview() {
  const time = timeAt(state.frame);
  renderAngleCards();
  const comparable = !state.predictionBusy && !state.ballOccluded && hasValue(state.predictedAngle) && hasValue(state.current?.armAngle);
  const delta = comparable ? Math.abs(state.predictedAngle - Number(state.current.armAngle)) : null;
  el("practiceError").textContent = delta == null ? (state.predictionBusy ? "Calculating difference…" : "Comparison unavailable") : `Off by ${delta.toFixed(1)}°`;
  el("practiceTip").textContent = delta == null
    ? (state.predictionBusy ? "Calculating your estimate…" : "Place all five points and calculate an estimate to compare with Statcast.")
    : delta > 15 ? "Estimate vs. Statcast. Check your release frame and landmark positions. Camera perspective can also affect the estimate."
    : "Absolute difference between your model estimate and the Statcast reference.";
  el("reviewSummary").replaceChildren();
  for (const [label,value] of [["Release",time == null ? `Frame ${state.frame}` : `${time.toFixed(3)} s · frame ${state.frame}`],
      ["Points",state.ballOccluded ? "4 body points · ball hidden" : `${POINTS.filter(n => state.points[n]).length} of 5 placed`],
      ["Timing",{unreviewed:"Not reviewed",closest_available:"Closest available",unclear:"Unclear"}[state.releaseQuality]],
      ["Clarity",{unreviewed:"Not reviewed",reviewed:"Reviewed",uncertain:"Uncertain"}[state.pointQuality]]]) {
    if (value === "Not reviewed") continue;
    const dt = document.createElement("dt"),dd = document.createElement("dd");dt.textContent = label;dd.textContent = value;el("reviewSummary").append(dt,dd);
  }
}
async function goReview() {
  if (blocked() || state.poseBusy || state.mode !== "points") return;
  const missing = (state.ballOccluded ? POSE_POINTS : POINTS).filter(n => !state.points[n]);
  if (missing.length) { toast(`Still need: ${missing.map(n => POINT_LABEL[n]).join(", ")}`); return; }
  state.reviewing = true; state.drag = null; stopPlayback(); renderWorkflow(); draw();
  if (ANALYSIS) {
    if (state.ballOccluded) el("anglePrediction").textContent = "Ball needed";
    else await predictFromPoints();
  }
}
function editPoints() { if (blocked()) return; state.reviewing = false; renderWorkflow(); draw(); }
function renderPointButtons() {
  el("reviewPoints").disabled = blocked() || state.poseBusy || !(state.ballOccluded ? POSE_POINTS : POINTS).every(n => state.points[n]);
  const count = POINTS.filter(n => state.points[n]).length;
  el("pointCount").textContent = `${count} of ${state.ballOccluded ? 4 : 5} points placed`;
  const next = state.active || nextEmptyPoint();
  el("pointPrompt").textContent = next ? `Place ${POINT_LABEL[next]}` : "Ready to review";
  el("pointButtons").replaceChildren();
  for (const [i, name] of POINTS.entries()) {
    const button = document.createElement("button");
    button.textContent = `${i+1} · ${POINT_LABEL[name]}${state.points[name] ? " ✓" : ""}`;
    button.className = `ghost${(state.active || nextEmptyPoint()) === name ? " active" : ""}`;
    button.disabled = blocked() || state.poseBusy || state.reviewing || (name === "ball" && state.ballOccluded);
    button.addEventListener("click", () => replacePoint(name));
    el("pointButtons").append(button);
  }
}
function frameUrl(frame, size = state.mode === "release" ? "scrub" : "full") {
  return `/api/frame?dir=${encodeURIComponent(state.current.dirName)}&frame=${frame}${HOSTED ? `&size=${size}&privacy=2` : ""}`;
}
function imageFor(url) {
  if (state.imgCache.has(url)) return state.imgCache.get(url);
  const promise = new Promise((resolve, reject) => {
    const image = new Image(); image.onload = () => resolve(image);
    image.onerror = () => { state.imgCache.delete(url); reject(new Error("This frame could not be loaded. Try selecting it again.")); };
    image.src = url;
  });
  state.imgCache.set(url, promise);
  while (state.imgCache.size > 40) state.imgCache.delete(state.imgCache.keys().next().value);
  return promise;
}
async function loadFrame() {
  const token = ++state.imageToken, url = frameUrl(state.frame);
  state.loading = true; state.img = null; renderWorkflow();
  try {
    const image = await imageFor(url);
    if (token !== state.imageToken) return false;
    state.img = image; draw();
    for (const offset of state.mode === "release" ? [-2,-1,1,2] : []) {
      const frame = state.frame + offset;
      if (frame >= 0 && frame < state.current.nFrames) imageFor(frameUrl(frame, "scrub")).catch(() => {});
    }
    return true;
  } catch (error) { if (token === state.imageToken) showError(error.message); return false; }
  finally { if (token === state.imageToken) { state.loading = false; renderWorkflow(); } }
}
async function showPitch(index = state.index) {
  stopPlayback(); stashDraft(); state.index = index; state.viewToken++; state.poseToken++; state.poseBusy = false;
  el("exportStatus").textContent = "";
  state.current = state.pitches[index]; state.imgCache.clear(); state.img = null;
  state.zoom = false; clearPoints(); state.dirty = false; showError();
  if (!state.current) return;
  el("app").hidden = false; el("intro").hidden = true; el("startPanel").hidden = true; el("reviewSaved").hidden = true; state.reviewing = false;
  const pitch = state.current, saved = state.labels.get(keyOf(pitch));
  state.releaseQuality = saved?.release_quality || "unreviewed"; state.pointQuality = saved?.point_quality || "unreviewed";
  state.mode = saved && saved.status !== "skipped" ? "points" : "release";
  state.frame = Math.max(0, Math.min(pitch.nFrames-1, initialFrame(pitch, saved)));
  if (saved && state.mode === "points") restorePoints(saved);
  const draft = state.drafts.get(keyOf(pitch));
  if (draft) Object.assign(state, JSON.parse(JSON.stringify(draft)));
  renderQueue(); renderProgress(); renderMeta(); renderHistogram();
  el("saveState").textContent = draft ? "Unsaved changes restored" : saved ? "Saved work restored" : "";
  state.loading = true; renderWorkflow();
  el("saveState").textContent = "Loading video frames…";
  try {
    if (!pitch.frameTimes) Object.assign(pitch, await request(`/api/clip?dir=${encodeURIComponent(pitch.dirName)}`));
    state.frame = Math.min(state.frame, pitch.nFrames-1);
    el("scrub").max = pitch.nFrames-1; el("scrub").value = state.frame;
    await loadFrame();
    el("saveState").textContent = draft ? "Unsaved changes restored" : saved ? "Saved work restored" : "Ready to choose release";
    if (state.mode === "points" && state.img) zoomToPitcher();
    renderThumbs();
    const next = state.pitches[index+1];
    if (next && !ANALYSIS) fetch(`/api/prefetch?dir=${encodeURIComponent(next.dirName)}`).catch(() => {});
  } catch (error) { showError(error.message); }
  finally { state.loading = false; renderWorkflow(); }
}
async function goToFrame(frame, {playback = false} = {}) {
  if (state.mode !== "release" || blocked()) return false;
  if (!playback) stopPlayback();
  const next = Math.max(0, Math.min(state.current.nFrames-1, Number(frame)));
  if (next !== state.frame) { clearPoints(); state.zoom = false; state.frame = next; state.releaseQuality = "unreviewed"; markDirty(); }
  el("scrub").value = state.frame; showError();
  const loaded = await loadFrame(); renderThumbs(); return loaded;
}
function renderThumbs() {
  el("thumbs").replaceChildren();
  for (let offset = -2; offset <= 2; offset++) {
    const frame = state.frame + offset;
    if (frame < 0 || frame >= state.current.nFrames) continue;
    const figure = document.createElement("figure"), button = document.createElement("button");
    const image = document.createElement("img"), caption = document.createElement("figcaption");
    image.src = frameUrl(frame, "scrub"); image.alt = `Frame ${frame}`;
    const delta = timeAt(frame) != null && timeAt(state.frame) != null ? Math.round((timeAt(frame)-timeAt(state.frame))*1000) : null;
    caption.textContent = `${frame} · ${offset === 0 ? "selected" : delta == null ? `${offset > 0 ? "+" : ""}${offset} frames` : `${delta > 0 ? "+" : ""}${delta} ms`}`;
    figure.className = offset === 0 ? "sel" : "";
    button.type = "button"; button.append(image, caption); button.addEventListener("click", () => goToFrame(frame));
    figure.append(button); el("thumbs").append(figure);
  }
}
function stopPlayback() { state.playing = false; state.playToken++; el("playPause").textContent = "Play"; }
async function togglePlay() {
  if (state.playing) { stopPlayback(); return; }
  if (blocked() || state.mode !== "release") return;
  if (!(state.current.fps > 0) && !state.current.frameTimes?.length) { showError("Frame timing is unavailable; use the arrow keys."); return; }
  state.playing = true; const token = ++state.playToken; renderWorkflow();
  let deadline = performance.now();
  while (state.playing && token === state.playToken && state.frame < state.current.nFrames-1) {
    const interval = (timeAt(state.frame+1)-timeAt(state.frame))*1000 / Number(el("playSpeed").value);
    deadline += interval;
    await new Promise(resolve => setTimeout(resolve, Math.max(1, deadline-performance.now())));
    if (!state.playing || token !== state.playToken) break;
    if (!await goToFrame(state.frame+1, {playback:true})) break;
  }
  if (token === state.playToken) { stopPlayback(); renderWorkflow(); }
}
/*
 * Zoom and pan.
 *
 * The canvas is always the size of the source frame and `draw` paints a
 * sub-rectangle of the image across all of it, so a view is just a rectangle
 * in image pixels. Points are stored in image coordinates either way: zooming
 * changes how precisely they can be clicked, never what gets saved.
 */
const MAX_ZOOM = 9;        // tightest view the mouse wheel will reach
const AUTO_MAX_ZOOM = 4;   // an automatic guess stays loose enough to argue with
const MIN_VIEW_PX = 120;   // never show fewer source pixels than this

// All views, clicks, saved points and CSV coordinates stay in original pixels.
// Only drawImage's source rectangle is scaled to the decoded bitmap resolution.
function imageDimensions() {
  return state.img ? {width:state.current?.imgW || state.img.width,
                      height:state.current?.imgH || state.img.height} : null;
}
function fullView() {
  const image = imageDimensions();
  return image ? { sx:0, sy:0, sw:image.width, sh:image.height } : { sx:0, sy:0, sw:1, sh:1 };
}
function clampView(view) {
  const image = imageDimensions();
  if (!image) return fullView();
  return { sx:Math.max(0, Math.min(view.sx, image.width - view.sw)),
           sy:Math.max(0, Math.min(view.sy, image.height - view.sh)), sw:view.sw, sh:view.sh };
}
/** The view centred on a focus, keeping the frame's aspect ratio so nothing stretches. */
function viewAround(focus, maxZoom = MAX_ZOOM) {
  const image = imageDimensions();
  if (!image || !focus) return fullView();
  const aspect = image.width / image.height;
  const tightest = Math.max(image.width / maxZoom, MIN_VIEW_PX);
  const sw = Math.min(image.width, Math.max(focus.halfW * 2, focus.halfH * 2 * aspect, tightest));
  const sh = sw / aspect;
  return clampView({ sx:focus.cx - sw / 2, sy:focus.cy - sh / 2, sw, sh });
}
function zoomTo(focus, maxZoom = MAX_ZOOM) {
  if (!focus || !state.img) return false;
  state.zoom = true; state.zoomView = viewAround(focus, maxZoom);
  draw(); renderWorkflow(); return true;
}
function resetZoom() { state.zoom = false; state.zoomView = null; state.pan = null; draw(); renderWorkflow(); }
/** Scale about a fixed image point, so whatever is under the cursor stays there. */
function zoomBy(factor, anchor) {
  const image = imageDimensions();
  if (!image || !factor) return;
  const view = viewTransform(), aspect = image.width / image.height;
  const sw = Math.min(image.width, Math.max(view.sw / factor, image.width / MAX_ZOOM, MIN_VIEW_PX));
  if (sw >= image.width - .5) { resetZoom(); return; }
  const sh = sw / aspect;
  const fx = (anchor.x - view.sx) / view.sw, fy = (anchor.y - view.sy) / view.sh;
  state.zoom = true;
  state.zoomView = clampView({ sx:anchor.x - fx * sw, sy:anchor.y - fy * sh, sw, sh });
  draw(); renderWorkflow();
}

/** Points already on screen. Exact, so it outranks every estimate below. */
function placedFocus() {
  const placed = POINTS.map(n => state.points[n]).filter(Boolean);
  if (!placed.length) return null;
  const xs = placed.map(p => p.x), ys = placed.map(p => p.y);
  const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  // Frame every placed point with room to reach the ones still missing.
  const half = Math.max(span * .85, 110);
  return { cx:(Math.min(...xs) + Math.max(...xs)) / 2, cy:(Math.min(...ys) + Math.max(...ys)) / 2,
           halfW:half, halfH:half };
}

/*
 * Where the pitcher is. Measured over the 192 labeled pitches in this
 * repository: every shoulder and ball point sits within x 0.07-0.68 and
 * y 0.34-0.87 of the frame, and 99% within x 0.13-0.65, y 0.37-0.81. The
 * delivery is left of centre and low, never in the top third, in broadcast
 * and in the side-on phone footage both. A fixed crop of that box holds the
 * pitcher without looking at a single pixel.
 */
const PITCHER_CROP = { cx:.39, cy:.59, halfW:.26, halfH:.22 };   // exactly that 99% box
function centerFocus() {
  const image = imageDimensions();
  if (!image) return null;
  return { cx:image.width * PITCHER_CROP.cx, cy:image.height * PITCHER_CROP.cy,
           halfW:image.width * PITCHER_CROP.halfW, halfH:image.height * PITCHER_CROP.halfH };
}

/**
 * Zoom once the release frame is locked, which is the moment the work turns
 * from watching a play into clicking joints a few pixels wide. Points already
 * placed frame themselves exactly; otherwise the crop is where pitchers are.
 */
function zoomToPitcher() {
  if (!state.img || state.mode !== "points") return;
  if (zoomTo(placedFocus())) return;
  zoomTo(centerFocus(), AUTO_MAX_ZOOM);
  if (!state.zoomHinted) {
    state.zoomHinted = true;
    toast("Zoomed in. Scroll to adjust, shift-drag to pan, Z for the full frame.");
  }
}
function fitZoom() { if (!zoomTo(placedFocus())) resetZoom(); }
function toggleZoom() { state.zoom ? resetZoom() : zoomToPitcher(); }
async function poseSeed({force = false} = {}) {
  if (blocked() || state.poseBusy || state.reviewing || state.mode !== "points" || !state.img) return;
  const targets = POSE_POINTS.filter(n => force || !state.points[n]);
  if (!targets.length) { toast("Body points are already placed. Shift+P replaces suggestions."); return; }
  const token = ++state.poseToken, view = state.viewToken, frame = state.frame;
  state.poseBusy = true; renderWorkflow(); el("saveState").textContent = "Suggesting body points…";
  try {
    const data = await request(`/api/pose?dir=${encodeURIComponent(state.current.dirName)}&frame=${frame}&throws=${encodeURIComponent(state.current.pThrows || "R")}`);
    if (token !== state.poseToken || view !== state.viewToken || frame !== state.frame || state.mode !== "points") return;
    if (!data.people?.length) throw new Error("No usable pose found. Click the points manually.");
    let person = data.people[0];
    if (state.points.shoulder) person = data.people.reduce((best, p) =>
      Math.hypot(p.shoulder.x-state.points.shoulder.x,p.shoulder.y-state.points.shoulder.y) <
      Math.hypot(best.shoulder.x-state.points.shoulder.x,best.shoulder.y-state.points.shoulder.y) ? p : best);
    for (const name of targets) {
      const point = person[name];
      if (point?.conf > 0.05 && point.x >= 0 && point.y >= 0 && point.x < state.current.imgW && point.y < state.current.imgH) {
        state.points[name] = {x:point.x, y:point.y}; state.origin[name] = "pose";
      }
    }
    state.active = nextEmptyPoint(); markDirty(); fitZoom();
    toast("Review the body points, then click the ball.");
  } catch (error) {
    if (token === state.poseToken) { el("seedWarn").hidden = false; el("seedWarn").textContent = error.message; }
  } finally {
    if (token === state.poseToken) { state.poseBusy = false; renderWorkflow(); el("saveState").textContent = state.dirty ? "Unsaved point suggestions" : "Release saved"; }
  }
}
function buildRow(status) {
  const pitch = state.current, row = {
    game_pk:pitch.gamePk, play_id:pitch.playId, frame_index:state.frame,
    img_w:pitch.imgW, img_h:pitch.imgH, park:pitch.park || "", true_arm_angle:pitch.armAngle ?? "",
    p_throws:pitch.pThrows || "R", venue_id:pitch.venueId ?? "", status,
    release_quality:state.releaseQuality, point_quality:["release_selected","skipped"].includes(status) ? "unreviewed" : state.pointQuality,
    fps:pitch.fps, release_time_sec:timeAt(state.frame), source_video:pitch.sourcePath || "",
    ball_visibility:status === "occluded" ? "occluded" : status === "labeled" ? "visible" : "",
    seeded:POINTS.some(n => state.origin[n] === "hand") ? "corrected" : "seed",
    provenance:POINTS.filter(n => state.points[n]).map(n => `${PREFIX[n]}:${state.origin[n] || "hand"}`).join(" ")
  };
  for (const name of POINTS) for (const axis of ["x","y"])
    row[`${PREFIX[name]}_${axis}`] = ["release_selected","skipped"].includes(status) || (name === "ball" && status === "occluded") ? "" : state.points[name]?.[axis]?.toFixed(2) ?? "";
  if (["release_selected","skipped"].includes(status)) row.provenance = row.seeded = "";
  return row;
}
async function persistRow(row) {
  if (blocked() || !state.img) return false;
  stopPlayback(); state.busy = true; showError(); renderWorkflow();
  const key = keyOf(state.current), previous = state.labels.get(key);
  try {
    const result = await request("/api/label", row);
    state.history.push({key, previous, index:state.index, undoToken:result.undoToken});
    state.labels.set(key, row); state.drafts.delete(key); state.dirty = false;
    el("saveState").textContent = row.status === "release_selected" ? "Release frame saved" : "Saved";
    renderProgress(); renderQueue(); renderHistogram(); return true;
  } catch (error) { showError(`Save failed: ${error.message} Your work is still on screen.`); return false; }
  finally { state.busy = false; renderWorkflow(); }
}
async function confirmRelease({next = false} = {}) {
  if (blocked() || state.mode !== "release" || !state.img) return;
  stopPlayback();
  const saved = state.labels.get(keyOf(state.current));
  // Reopening a confirmed frame must never downgrade existing hand labels.
  const alreadySaved = saved && saved.status !== "skipped" && Number(saved.frame_index) === state.frame;
  if (!alreadySaved && !await persistRow(buildRow("release_selected"))) return;
  if (alreadySaved && state.releaseQuality !== (saved.release_quality || "unreviewed")) {
    const hasPointDraft = state.dirty && POINTS.some(n => state.points[n]);
    if (!await persistRow({...saved, release_quality:state.releaseQuality})) return;
    if (hasPointDraft) markDirty();
  }
  if (next) { state.dirty = false; state.drafts.delete(keyOf(state.current)); await advance("release"); return; }
  state.mode = "points";
  if (alreadySaved && !POINTS.some(n => state.points[n])) restorePoints(saved);
  if (!await loadFrame()) return;
  renderWorkflow(); draw();
  zoomToPitcher();
}
async function saveCurrent(status = "labeled") {
  if (blocked() || state.poseBusy || !state.img) return;
  if (status !== "skipped") {
    if (state.mode !== "points") { await confirmRelease(); return; }
    if (ANALYSIS && !state.ballOccluded && (state.predictionBusy || state.predictedAngle == null)) {
      toast("Review the estimate before saving the analysis."); return;
    }
    const missing = (state.ballOccluded ? POSE_POINTS : POINTS).filter(n => !state.points[n]);
    if (missing.length) { toast(`Still need: ${missing.map(n => POINT_LABEL[n]).join(", ")}`); return; }
    status = state.ballOccluded ? "occluded" : "labeled";
  }
  if (await persistRow(buildRow(status))) {
    if (ANALYSIS && status !== "skipped") { state.reviewing = true; el("reviewSaved").hidden = false; renderWorkflow(); toast("Analysis saved."); }
    else await advance("points");
  }
}
async function advance(stage) {
  const order = [...state.pitches.slice(state.index+1), ...state.pitches.slice(0,state.index)];
  const next = order.find(p => {
    if (!visiblePitches().includes(p)) return false;
    const row = state.labels.get(keyOf(p));
    return stage === "release" ? !row : !isComplete(row);
  });
  if (next) await showPitch(state.pitches.indexOf(next));
  else {
    if (stage === "release") { state.mode = "points"; await loadFrame(); renderWorkflow(); draw(); }
    toast(stage === "release" ? "All releases selected. Use “Need points” to finish labeling." : "Queue complete. Add another pitch or choose one to review.");
  }
}
function changeRelease() {
  if (blocked() || state.poseBusy) return;
  clearPrediction(); state.reviewing = false; stopPlayback(); state.mode = "release";
  state.zoom = false; state.zoomView = null; state.pan = null;
  state.poseToken++; state.pickingPitcher = false;
  renderWorkflow(); renderThumbs(); draw();
  toast("Changing frames clears point positions. Your saved label stays intact until you confirm.");
}
function replacePoint(name) {
  if (blocked() || state.poseBusy || state.mode !== "points" || (name === "ball" && state.ballOccluded)) return;
  state.points[name] = null; state.origin[name] = null; state.active = name; state.placingPoint = true; markDirty(); draw();
}
async function undo() {
  if (blocked() || !state.history.length) return;
  stopPlayback(); state.busy = true; renderWorkflow();
  const item = state.history[state.history.length-1];
  try {
    await request("/api/undo", {undoToken:item.undoToken});
    state.history.pop();
    if (item.previous) state.labels.set(item.key,item.previous); else state.labels.delete(item.key);
    state.drafts.delete(item.key); state.dirty = false;
    state.busy = false; await showPitch(item.index); toast("Last save undone.");
  } catch (error) { showError(`Undo failed: ${error.message}`); }
  finally { state.busy = false; renderWorkflow(); }
}
async function addLocalVideo(payload) {
  if (blocked()) return;
  stopPlayback(); state.busy = true; renderWorkflow(); el("importSubmit").disabled = true;
  try {
    const data = await request("/api/import", payload);
    state.pitches.push(data.pitch); state.sessionFilter = data.pitch.sessionId || ""; el("queueFilter").value = "local";
    el("importPanel").hidden = true; state.busy = false;
    await showPitch(state.pitches.length-1);
  } catch (error) { el("importError").textContent = error.message; el("importError").hidden = false; showError(error.message); }
  finally { state.busy = false; el("importSubmit").disabled = false; renderWorkflow(); }
}
function viewTransform() {
  return state.zoom && state.zoomView ? state.zoomView : fullView();
}

function draw() {
  const image = state.img;
  if (!image || !image.width) return;
  const view = viewTransform();
  const dimensions = imageDimensions();
  canvas.width = dimensions.width; canvas.height = dimensions.height;
  const scaleX = image.width / dimensions.width, scaleY = image.height / dimensions.height;
  ctx.drawImage(image, view.sx * scaleX, view.sy * scaleY, view.sw * scaleX, view.sh * scaleY,
                0, 0, canvas.width, canvas.height);

  const scale = canvas.width / view.sw;
  const toCanvas = (p) => ({ x:(p.x - view.sx) * scale, y:(p.y - view.sy) * (canvas.height / view.sh) });
  const P = state.mode === "points" ? state.points : {};

  const line = (a, b, color, width, dash) => {
    if (!a || !b) return;
    const p = toCanvas(a), q = toCanvas(b);
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1.5, width * scale);
    ctx.setLineDash(dash ? dash.map((d) => d * scale) : []);
    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke();
    ctx.setLineDash([]);
  };

  // Torso: shoulder line. Its length vs the arm gives torso rotation.
  line(P.shoulder, P.offShoulder, "rgba(247,140,107,.75)", 2.2);
  // Arm chain.
  line(P.shoulder, P.elbow, "rgba(255,255,255,.35)", 1.6);
  line(P.elbow, P.wrist, "rgba(255,255,255,.35)", 1.6);
  // Legacy measure: shoulder -> wrist.
  line(P.shoulder, P.wrist, "rgba(255,255,255,.55)", 1.8, [8, 6]);
  // Primary measure: shoulder -> ball, which is how Statcast defines arm_angle.
  line(P.shoulder, P.ball, "rgba(255,110,199,.95)", 2.6);

  if (P.shoulder) {
    const a = toCanvas(P.shoulder);
    ctx.strokeStyle = "rgba(88,166,255,.4)";
    ctx.lineWidth = Math.max(1, 1.2 * scale);
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(0, a.y); ctx.lineTo(canvas.width, a.y); ctx.stroke();
  }

  for (const name of POINTS) {
    const point = P[name];
    if (!point) continue;
    const p = toCanvas(point);
    const radius = Math.max(5, 6.5 * scale);
    ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = POINT_COLOR[name]; ctx.fill();
    ctx.lineWidth = Math.max(1.5, 2 * scale); ctx.strokeStyle = "#000"; ctx.stroke();
    ctx.beginPath(); ctx.arc(p.x, p.y, radius * .28, 0, Math.PI * 2);
    ctx.fillStyle = "#000"; ctx.fill();
    // Ring = still the model's guess rather than yours.
    if (state.origin[name] === "pose") {
      ctx.beginPath(); ctx.arc(p.x, p.y, radius * 1.75, 0, Math.PI * 2);
      ctx.strokeStyle = POINT_COLOR[name];
      ctx.lineWidth = Math.max(1, 1.3 * scale); ctx.stroke();
    }
  }

  // Prompt for whatever is next to place.
  const pending = state.mode === "points" && !state.reviewing ? (state.active || nextEmptyPoint()) : null;
  if (pending) {
    ctx.font = `${Math.max(15, 17 * scale)}px -apple-system, system-ui, sans-serif`;
    ctx.fillStyle = POINT_COLOR[pending];
    ctx.strokeStyle = "rgba(0,0,0,.85)";
    ctx.lineWidth = Math.max(2, 3 * scale);
    const text = `click: ${POINT_LABEL[pending]}`;
    ctx.strokeText(text, 14 * scale, 26 * scale);
    ctx.fillText(text, 14 * scale, 26 * scale);
  }

  renderMeasure();
  renderPointButtons();

}

/**
 * Pixel arm angle measured at the shoulder, up from horizontal, in image
 * coordinates (y grows downward, hence the negated dy). Straight overhead is
 * +90, sidearm is 0, submarine negative -- the sign convention Statcast uses.
 *
 * `to` selects the far endpoint. Statcast derives arm_angle from the shoulder
 * to the BALL release point, so "ball" is the definitive measure and "wrist"
 * is the proxy the first 100 pitches were labeled with.
 */
function pixelAngle(to = "ball") {
  const shoulder = state.points.shoulder, far = state.points[to];
  if (!shoulder || !far) return null;
  return Math.atan2(-(far.y - shoulder.y), Math.abs(far.x - shoulder.x)) * 180 / Math.PI;
}

/**
 * Gross-error guard, checked against the ball-based angle.
 *
 * Statcast truth is known at label time, so a structural mistake shows up
 * immediately. Three of the first 100 labels had shoulder and wrist reversed,
 * which mirrors the angle's sign and produced 30-48 deg errors; every other
 * label landed within 7.6 deg. The threshold sits far above that spread so it
 * catches swaps, wrong-person seeds and wrong-frame picks without inviting
 * anyone to nudge points toward the truth.
 */
const SANITY_LIMIT = 15;

function renderMeasure() {
  const fmt = (p) => p ? `${p.x.toFixed(0)}, ${p.y.toFixed(0)}` : "—";
  el("pShoulder").textContent = fmt(state.points.shoulder);
  el("pOffShoulder").textContent = fmt(state.points.offShoulder);
  el("pElbow").textContent = fmt(state.points.elbow);
  el("pWrist").textContent = fmt(state.points.wrist);
  el("pBall").textContent = fmt(state.points.ball);

  const ballAngle = pixelAngle("ball");
  const wristAngle = pixelAngle("wrist");
  el("pAngle").textContent = ballAngle === null ? "—" : `${ballAngle.toFixed(1)}°`;
  el("pAngleWrist").textContent = wristAngle === null ? "—" : `${wristAngle.toFixed(1)}°`;
  renderSanity(ballAngle !== null ? ballAngle : wristAngle, ballAngle === null);
}

function renderSanity(angle, isProxy) {
  const truth = state.current && state.current.armAngle != null ? +state.current.armAngle : null;
  const box = el("sanityWarn");
  if (ANALYSIS) { box.hidden = true; return; }
  if (!showsReference() || !state.reviewing || angle === null || truth === null) {
    el("pDelta").textContent = "—"; box.hidden = true; return;
  }
  const delta = angle - truth;
  el("pDelta").textContent = `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}°${isProxy ? " (wrist)" : ""}`;
  el("pDelta").style.color = Math.abs(delta) > SANITY_LIMIT ? "#ff9d9d" : "";

  if (Math.abs(delta) <= SANITY_LIMIT) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = Math.abs(angle + truth) < 8
    ? `Off by ${delta.toFixed(0)}° and sign-mirrored — shoulder and wrist look <b>swapped</b>. Press <kbd>X</kbd>.`
    : `Off by ${delta.toFixed(0)}° from Statcast. Check the release frame and that the points are on the pitcher.`;
}

function renderMeta() {
  const pitch = state.current;
  el("meta").replaceChildren();
  for (const [name, value] of [["Pitcher", pitch.pitcherName || "—"], ["Throws", pitch.pThrows || "—"],
      ["Park / session", pitch.park || "—"], ["Source", pitch.sourceName || "MLB clip"],
      ["Frame rate", pitch.fps ? `${pitch.fps.toFixed(2)} fps` : "Unknown"],
      ["Frame spacing", pitch.fps > 0 ? `About ${(1000/pitch.fps).toFixed(1)} ms` : "Unknown"]]) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = name; dd.textContent = value; el("meta").append(dt, dd);
  }
}

function renderProgress() {
  if (ANALYSIS) renderSession();
  const pitches = visiblePitches();
  const done = pitches.filter(p => isComplete(state.labels.get(keyOf(p)))).length;
  el("progress").textContent = `${done} / ${pitches.length} complete`;
}
function selectedSessionId() {
  return state.current?.sessionId || state.sessionFilter || state.pitches[0]?.sessionId || "";
}
function sessionHasDrafts(session) {
  return state.pitches.some(p => p.sessionId === session &&
    (state.drafts.has(keyOf(p)) || (p === state.current && state.dirty)));
}
function canExportSession() {
  const session = selectedSessionId();
  return !!session && !blocked() && !state.exporting && !sessionHasDrafts(session) &&
    state.pitches.some(p => p.sessionId === session && state.labels.has(keyOf(p)));
}
function renderSession() {
  const session = selectedSessionId(), practice = ANALYSIS && el("queueFilter").value === "batch";
  const pitches = state.pitches.filter(p => practice ? (p.example || p.uiTestBatch) : p.sessionId === session);
  el("sessionPanel").hidden = !state.pitches.length;
  el("sessionSelect").replaceChildren();
  const sessions = new Map(state.pitches.map(p => [p.sessionId, p]));
  for (const [id, pitch] of sessions) {
    const option = document.createElement("option"); option.value = id;
    option.textContent = pitch.sessionName || pitch.sourceName || "MLB example";
    option.selected = id === session; el("sessionSelect").append(option);
  }
  el("sessionPitches").replaceChildren();
  for (const [index, pitch] of pitches.entries()) {
    const row = state.labels.get(keyOf(pitch)), button = document.createElement("button");
    const status = {labeled:"Labeled", occluded:"Ball unavailable", release_selected:"Release saved · needs points", skipped:"Skipped"}[row?.status] || "Needs release";
    button.className = `secondary${pitch === state.current ? " selectedPitch" : ""}`;
    button.textContent = `Pitch ${index+1}${practice ? ` · ${pitch.pitcherName}` : ""} · ${status}${hasValue(row?.frame_index) ? ` · frame ${row.frame_index}` : ""}`;
    button.disabled = blocked();
    button.addEventListener("click", () => { if (!blocked()) showPitch(state.pitches.indexOf(pitch)); });
    el("sessionPitches").append(button);
  }
  const saved = pitches.filter(p => isComplete(state.labels.get(keyOf(p)))).length;
  el("sessionSummary").textContent = `${saved} complete / ${pitches.length} pitches · ${practice ? "MLB practice · choose any pitch below" : pitches[0]?.sourceName || "MLB example"}`;
  el("exportSession").textContent = practice ? "Export pitch CSV" : "Export session CSV";
  el("exportSession").disabled = !canExportSession();
}
async function exportSession() {
  if (!canExportSession()) {
    el("exportStatus").textContent = "Save your edits or a release frame before exporting this session."; return;
  }
  const session = selectedSessionId();
  state.exporting = true; renderWorkflow(); el("exportStatus").textContent = "Preparing CSV from saved points…";
  try {
    const response = await fetch(`/api/export?session=${encodeURIComponent(session)}`);
    if (!response.ok) { const error = await response.json(); throw new Error(error.error || "Export failed."); }
    // A normal HTTP attachment also works in browsers that don't download blob URLs.
    await response.text();
    el("csvFallback").href = `/api/export?session=${encodeURIComponent(session)}`;
    el("csvFallback").dataset.session = session;
    el("csvFallback").hidden = false;
    el("csvFallback").click();
    el("exportStatus").textContent = "CSV ready. If the download did not start, use Download CSV directly. Unfinished pitches have blank predictions.";
  } catch (error) { el("exportStatus").textContent = `Export failed: ${error.message}`; }
  finally { state.exporting = false; renderWorkflow(); }
}
function sendVideo(file, metadata) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", HOSTED ? "/api/upload" : `/api/upload?${new URLSearchParams(metadata)}`);
    xhr.setRequestHeader("X-CSRF-Token", csrfToken());
    // Allow a large upload on a slow connection, then server preparation and storage.
    if (!HOSTED) xhr.setRequestHeader("Content-Type", "video/mp4");
    xhr.timeout = HOSTED ? 900000 : 180000;
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) el("uploadProgress").value = event.loaded / event.total * 100;
      el("uploadStatus").textContent = `Uploading… ${Math.round(el("uploadProgress").value)}%`;
    };
    xhr.upload.onload = () => { el("uploadProgress").removeAttribute("value"); el("uploadStatus").textContent = "Checking video frames…"; };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status < 200 || xhr.status >= 300 || !data.ok) throw new Error(data.error || "Upload failed.");
        resolve(data);
      } catch (error) { reject(error); }
    };
    xhr.onerror = () => reject(new Error("Connection lost. Try the upload again."));
    xhr.ontimeout = () => reject(new Error("Upload timed out. Try a shorter clip."));
    if (HOSTED) {
      const body = new FormData();
      body.append("video", file, file.name);
      for (const [key, value] of Object.entries(metadata)) {
        if (key !== "filename") body.append(key, value);
      }
      xhr.send(body);
    } else xhr.send(file);
  });
}
async function uploadVideo() {
  if (blocked()) return;
  const file = el("videoFile").files?.[0];
  const maxMB = HOSTED ? 200 : 100;
  if (!file || !/\.mp4$/i.test(file.name) || file.size <= 0 || file.size > maxMB * 1024 * 1024) {
    el("importError").textContent = `Choose an MP4 video no larger than ${maxMB} MB.`; el("importError").hidden = false; return;
  }
  stopPlayback(); stashDraft(); state.busy = true; renderWorkflow();
  el("importSubmit").disabled = true; el("uploadProgress").hidden = false; el("uploadProgress").value = 0;
  el("importError").hidden = true; el("uploadStatus").textContent = "Starting upload…";
  try {
    const data = await sendVideo(file, {filename:file.name, sessionName:el("importSession").value.trim(),
      pitcherName:el("importPitcher").value.trim(), pThrows:el("importThrows").value});
    state.pitches.push(data.pitch); state.sessionFilter = data.pitch.sessionId;
    el("queueFilter").value = "local"; el("importPanel").hidden = true;
    el("uploadStatus").textContent = "Upload ready."; el("videoFile").value = "";
    state.busy = false; await showPitch(state.pitches.length-1);
  } catch (error) {
    el("importError").textContent = error.message; el("importError").hidden = false;
    el("uploadStatus").textContent = "Upload not completed.";
  } finally {
    state.busy = false; el("importSubmit").disabled = false; el("uploadProgress").hidden = true; renderWorkflow();
  }
}
function renderHistogram() {
  const bins = new Array(12).fill(0), lo = -70, hi = 90;
  for (const row of state.labels.values()) {
    if (row.status !== "labeled" || !row.ball_x) continue;
    const angle = parseFloat(row.true_arm_angle);
    if (Number.isNaN(angle)) continue;
    bins[Math.max(0, Math.min(11, Math.floor((angle - lo) / (hi - lo) * 12)))]++;
  }
  const max = Math.max(1, ...bins);
  el("hist").innerHTML = bins.map((n) => `<div style="height:${n / max * 100}%" title="${n}"></div>`).join("");
  const n = bins.reduce((a, b) => a + b, 0);
  el("histNote").textContent = `${lo}° to ${hi}° true arm angle · ${n} complete (5-point)`;
}


function eventToImage(event) {
  const rect = canvas.getBoundingClientRect();
  const view = viewTransform();
  const cx = (event.clientX - rect.left) * (canvas.width / rect.width);
  const cy = (event.clientY - rect.top) * (canvas.height / rect.height);
  return { x: view.sx + cx * (view.sw / canvas.width), y: view.sy + cy * (view.sh / canvas.height) };
}

function nearestHandle(point) {
  const view = viewTransform();
  const tolerance = 12 * view.sw / canvas.getBoundingClientRect().width;
  let best = null, bestDist = Infinity;
  for (const name of POINTS) {
    const candidate = state.points[name];
    if (!candidate) continue;
    const distance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
    if (distance < bestDist) { bestDist = distance; best = name; }
  }
  return bestDist <= tolerance ? best : null;
}



async function predictFromPoints() {
  if (blocked() || state.poseBusy || state.mode !== "points" || state.ballOccluded || !POINTS.every(n => state.points[n])) return;
  const token = ++state.predictionToken, view = state.viewToken;
  state.predictionBusy = true; el("anglePrediction").textContent = "Calculating…"; renderWorkflow();
  try {
    const data = await request("/api/predict-angle", buildRow("labeled"));
    if (token !== state.predictionToken || view !== state.viewToken) return;
    state.predictedAngle = Number(data.predicted_arm_angle);
    el("anglePrediction").textContent = `${state.predictedAngle.toFixed(1)}°`;
  } catch (error) {
    if (token === state.predictionToken) el("anglePrediction").textContent = `Unavailable: ${error.message}`;
  } finally { if (token === state.predictionToken) { state.predictionBusy = false; renderWorkflow(); } }
}
el("predictAngle").addEventListener("click", predictFromPoints);
el("releaseQuality").addEventListener("change", event => { state.releaseQuality = event.target.value; markDirty(); renderWorkflow(); });
el("pointQuality").addEventListener("change", event => { state.pointQuality = event.target.value; markDirty(); renderWorkflow(); });

canvas.addEventListener("pointerdown", event => {
  if (blocked() || !state.img) return;
  // Shift-drag pans instead of placing: a plain click must stay a click.
  if (state.zoom && (event.shiftKey || event.button === 1)) {
    const view = viewTransform(), rect = canvas.getBoundingClientRect();
    state.pan = { x:event.clientX, y:event.clientY, view,
                  scale:view.sw / (rect.width || canvas.width || 1) };
    canvas.setPointerCapture(event.pointerId); canvas.style.cursor = "grabbing";
    event.preventDefault(); return;
  }
  if (state.poseBusy || state.reviewing || state.mode !== "points") return;
  // While collecting landmarks, nearby clicks must place the next point rather
  // than silently starting a drag on the previous one. Completed sets can drag.
  const point = eventToImage(event), handle = state.placingPoint || nextEmptyPoint() ? null : nearestHandle(point);
  if (handle) { state.drag = handle; canvas.setPointerCapture(event.pointerId); return; }
  const target = state.active || nextEmptyPoint();
  if (!target) return;
  state.placingPoint = false; state.points[target] = point; state.origin[target] = "hand"; state.active = nextEmptyPoint(); markDirty(); draw();
  if (target === "shoulder" && state.pickingPitcher) { state.pickingPitcher = false; poseSeed(); }
});
canvas.addEventListener("pointermove", event => {
  if (state.pan) {
    const { x, y, view, scale } = state.pan;
    state.zoomView = clampView({ sx:view.sx - (event.clientX - x) * scale,
                                 sy:view.sy - (event.clientY - y) * scale, sw:view.sw, sh:view.sh });
    draw(); return;
  }
  if (!state.drag || blocked() || state.poseBusy || state.reviewing) return;
  const point = eventToImage(event);
  point.x = Math.max(0,Math.min(imageDimensions().width-0.01,point.x)); point.y = Math.max(0,Math.min(imageDimensions().height-0.01,point.y));
  state.points[state.drag] = point; state.origin[state.drag] = "hand"; markDirty(); draw();
});
for (const event of ["pointerup","pointercancel","lostpointercapture"])
  canvas.addEventListener(event, () => { state.drag = null; state.pan = null; canvas.style.cursor = ""; });
// Wheel and trackpad pinch zoom around the cursor, once the release is locked.
canvas.addEventListener("wheel", event => {
  if (blocked() || state.mode !== "points" || !state.img) return;
  event.preventDefault();
  zoomBy(Math.exp(-event.deltaY * (event.ctrlKey ? .01 : .0015)), eventToImage(event));
}, { passive:false });
el("scrub").addEventListener("input", event => goToFrame(Number(event.target.value)));
el("playPause").addEventListener("click", togglePlay);
el("confirmRelease").addEventListener("click", () => confirmRelease());
el("releaseNext").addEventListener("click", () => confirmRelease({next:true}));
el("savePoints").addEventListener("click", () => saveCurrent());
el("nextReviewPitch").addEventListener("click", continueAnalysis);
el("skipPitch").addEventListener("click", () => saveCurrent("skipped"));
el("undoSave").addEventListener("click", undo);
el("seedPoints").addEventListener("click", () => poseSeed());
el("changeRelease").addEventListener("click", changeRelease);
el("zoomPoints").addEventListener("click", toggleZoom);
el("choosePitcher").addEventListener("click", () => {
  clearPoints(); state.zoom = false; state.pickingPitcher = true; state.active = "shoulder"; markDirty(); draw();
  toast("Click the pitcher's throwing shoulder. The other body points will be suggested.");
});
el("ballOccluded").addEventListener("change", event => {
  state.ballOccluded = event.target.checked;
  if (state.ballOccluded) { state.points.ball = null; state.origin.ball = null; }
  state.active = nextEmptyPoint(); markDirty(); draw();
});
function movePitch(direction) {
  if (blocked()) return;
  const queue = visiblePitches(), index = queue.indexOf(state.current), target = queue[index + direction];
  if (target) showPitch(state.pitches.indexOf(target));
}
el("prevPitch").addEventListener("click", () => movePitch(-1));
el("nextPitch").addEventListener("click", () => movePitch(1));
el("pitchSelect").addEventListener("change", event => {
  if (!blocked()) showPitch(state.pitches.findIndex(p => keyOf(p) === event.target.value));
});
el("queueFilter").addEventListener("change", () => {
  state.sessionFilter = "";
  const pitches = visiblePitches();
  if (pitches.length && !pitches.includes(state.current)) showPitch(state.pitches.indexOf(pitches[0]));
  else { renderQueue(); renderProgress(); if (!pitches.length) toast("No pitches in this queue."); }
});
el("openImport").addEventListener("click", () => { stopPlayback(); el("importPanel").hidden = false; el("videoFile").focus(); });
el("closeImport").addEventListener("click", () => { el("importPanel").hidden = true; if (!el("startPanel").hidden) el("startVideo").focus(); });
el("importPanel").addEventListener("submit", event => {
  event.preventDefault(); el("importError").hidden = true;
  uploadVideo();
});
el("newLocalPitch").addEventListener("click", () => {
  const p = state.current;
  if (state.dirty) { showError("Save the release or point labels before adding another pitch from this video."); return; }
  addLocalVideo({parentId:p.playId, startFrame:Math.min(state.frame+1,p.nFrames-1)});
});
document.addEventListener("keydown", event => {
  if (el("app").hidden || ["INPUT","SELECT","TEXTAREA"].includes(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (["BUTTON","A"].includes(event.target.tagName) && ["enter"," "].includes(key)) return;
  if (key === " " && state.playing) { event.preventDefault(); stopPlayback(); return; }
  if (blocked()) return;
  if (["arrowleft","arrowright","enter"," "].includes(key)) event.preventDefault();
  if (key === "arrowleft" || key === "arrowright") {
    if (state.mode === "points") { toast("Release is locked. Use “Change release frame” first."); return; }
    goToFrame(state.frame + (event.shiftKey ? 10 : 1)*(key === "arrowright" ? 1 : -1)); return;
  }
  if (key === " ") { togglePlay(); return; }
  if (key === "enter") { state.mode === "release" ? confirmRelease() : state.reviewing ? (ANALYSIS && !state.dirty && isComplete(state.labels.get(keyOf(state.current))) ? continueAnalysis() : saveCurrent()) : goReview(); return; }
  if (key === "u") { undo(); return; }
  if (key === "s") { saveCurrent("skipped"); return; }
  // Zoom works wherever the frame is on screen, review included.
  if (key === "z") { if (state.img && !state.poseBusy) toggleZoom(); return; }
  if (["+","=","-","_"].includes(key)) {
    if (!state.img || state.poseBusy) return;
    const view = viewTransform();
    zoomBy(["+","="].includes(key) ? 1.4 : 1/1.4, { x:view.sx + view.sw/2, y:view.sy + view.sh/2 });
    return;
  }
  if (state.mode !== "points" || state.poseBusy || state.reviewing) return;
  if (key === "p" && !ANALYSIS) poseSeed({force:event.shiftKey});
  if (key >= "1" && key <= "5") replacePoint(POINTS[Number(key)-1]);
  if (key === "c") { clearPoints(); markDirty(); draw(); }
  if (key === "x" && state.points.shoulder && state.points.wrist) {
    [state.points.shoulder,state.points.wrist] = [state.points.wrist,state.points.shoulder];
    state.origin.shoulder = state.origin.wrist = "hand"; markDirty(); draw();
  }
});
window.addEventListener("beforeunload", event => {
  if (state.dirty || state.drafts.size) { event.preventDefault(); event.returnValue = ""; }
});
function configureWorkspace() {
  el("pitchNavigation").open = !ANALYSIS;
  document.title = ANALYSIS ? "Arm Angle CV · Pitch analysis" : "Arm Angle CV · Training labeler";
  el("analysisNav").className = ANALYSIS ? "active" : "";
  el("trainingNav").className = ANALYSIS ? "" : "active";
  el("workspaceBadge").textContent = ANALYSIS ? "YOUR FOOTAGE" : "DATASET WORKSPACE";
  el("workspaceEyebrow").textContent = ANALYSIS ? "UNDERSTAND YOUR DELIVERY" : "BUILD THE DATASET";
  el("workspaceTitle").textContent = ANALYSIS ? "Pitch analysis" : "Training labeler";
  el("workspaceDescription").textContent = ANALYSIS ? "Upload → lock release → mark five points → save → export CSV." : "One pitch at a time. Choose the release, mark the points, then review and save.";
  const options = HOSTED ? [["local","My footage"],["batch","MLB examples"]] : ANALYSIS ? [["local","My footage"],["batch","MLB examples"],["all","All available clips"]]
    : [["batch","New MLB trial batch"],["release","Need release"],["points","Need points"],["all","All MLB pitches"]];
  el("queueFilter").replaceChildren();
  for (const [value,label] of options) { const o = document.createElement("option");o.value = value;o.textContent = label;el("queueFilter").append(o); }
  el("queueFilter").value = ANALYSIS ? "local" : (state.pitches.some(p => p.uiTestBatch) ? "batch" : "release");
  if (HOSTED) {
    el("startExample").hidden = !HAS_EXAMPLE;
    const count = Number(document.documentElement.dataset.exampleCount) || 5;
    el("exampleTitle").textContent = "Try an example →";
    el("exampleDescription").textContent = `Explore the steps with ${count} MLB ${count === 1 ? "pitch" : "pitches"}. No video needed.`;
  }
}
el("prevFrame").addEventListener("click", () => goToFrame(state.frame-1));
el("nextFrame").addEventListener("click", () => goToFrame(state.frame+1));
el("reviewPoints").addEventListener("click", goReview);
el("editPoints").addEventListener("click", editPoints);
el("startVideo").addEventListener("click", () => el("openImport").click());
// Example clones are stored like uploads, so "your uploads" excludes them.
const ownUploads = () => state.pitches.filter(p => p.isLocal && !p.example);
el("startLocal").addEventListener("click", () => {
  if (blocked()) return;
  const uploads = ownUploads(), session = uploads.at(-1)?.sessionId;
  if (!session) return;
  const pitches = uploads.filter(p => p.sessionId === session);
  const next = pitches.find(p => !isComplete(state.labels.get(keyOf(p)))) || pitches[0];
  el("queueFilter").value = "local"; state.sessionFilter = session;
  showPitch(state.pitches.indexOf(next));
});
async function openExample() {
  if (blocked()) return;
  if (!HOSTED) {
    state.sessionFilter = ""; el("queueFilter").value = "batch";
    const local = state.pitches.find(p => p.uiTestBatch);
    if (local) await showPitch(state.pitches.indexOf(local));
    return;
  }
  // Hosted visitors get copies of the shared clips of their own, so practice
  // annotations, exports and deletions stay inside their own workspace.
  el("startExample").disabled = true; state.busy = true;
  try {
    const data = await request("/api/example", {});
    state.busy = false;
    const pitches = data.pitches || [data.pitch];
    for (const pitch of pitches) {
      if (!state.pitches.some(p => p.playId === pitch.playId)) state.pitches.push(pitch);
    }
    state.sessionFilter = ""; el("queueFilter").value = "batch";
    const next = pitches.find(p => !isComplete(state.labels.get(keyOf(p)))) || pitches[0];
    await showPitch(state.pitches.findIndex(p => p.playId === next.playId));
  } catch (error) {
    state.busy = false; el("startExample").disabled = false;
    el("intro").hidden = false; el("intro").textContent = `Could not open the example: ${error.message}`;
  } finally { state.busy = false; el("startExample").disabled = false; }
}
el("startExample").addEventListener("click", openExample);
el("downloadResult").addEventListener("click", exportSession);
el("exportSession").addEventListener("click", exportSession);
el("sessionSelect").addEventListener("change", async event => {
  state.sessionFilter = event.target.value;
  const pitch = state.pitches.find(p => p.sessionId === state.sessionFilter);
  if (pitch) {
    el("queueFilter").value = pitch.example || !pitch.isLocal ? "batch" : "local";
    if (pitch.example) state.sessionFilter = "";
    await showPitch(state.pitches.indexOf(pitch));
  }
});
async function boot() {
  configureWorkspace();
  try {
    const [pitches, labels] = await Promise.all([request("/api/pitches"),request("/api/labels")]);
    state.pitches = pitches.pitches;
    for (const row of labels.labels) state.labels.set(`${row.game_pk}_${row.play_id}`,row);
    configureWorkspace();renderQueue();renderProgress();
    if (ANALYSIS) {
      el("intro").hidden = true;el("startPanel").hidden = false;
      el("startExample").disabled = HOSTED ? !HAS_EXAMPLE : !state.pitches.some(p => p.uiTestBatch);
      const uploads = ownUploads(), sessions = new Set(uploads.map(p => p.sessionId)).size;
      el("startLocal").hidden = !uploads.length;
      el("startLocalDescription").textContent = `Reopen ${sessions === 1 ? "your saved video" : `your latest of ${sessions} saved videos`}. Switch videos from the session menu.`;
      return;
    }
    if (!state.pitches.length) { el("intro").textContent = "No MLB clips yet. Add a pitch batch to begin labeling."; return; }
    let queue = visiblePitches();
    if (!queue.length) { el("queueFilter").value = "all";queue = visiblePitches(); }
    const first = queue.find(p => !isComplete(state.labels.get(keyOf(p)))) || queue[0];
    await showPitch(state.pitches.indexOf(first));
  } catch (error) { el("intro").textContent = `Could not load the workspace: ${error.message}`; }
}
boot();

"use strict";

const WORKSPACE = document.documentElement?.dataset.workspace === "analysis" ? "analysis" : "training";
const ANALYSIS = WORKSPACE === "analysis";

const state = {
  pitches: [], index: 0, current: null, labels: new Map(), history: [], drafts: new Map(),
  frame: 0, img: null, imgCache: new Map(), points: {}, origin: {}, mode: "release",
  zoom: false, zoomView: null, drag: null, active: null, placingPoint: false, ballOccluded: false,
  loading: false, busy: false, poseBusy: false, playing: false, playToken: 0,
  reviewing: false, predictedAngle: null, predictionBusy: false,
  releaseQuality: "unreviewed", pointQuality: "unreviewed", predictionToken: 0,
  viewToken: 0, imageToken: 0, poseToken: 0, pickingPitcher: false, dirty: false
};
const POINTS = ["shoulder", "offShoulder", "elbow", "wrist", "ball"];
const POSE_POINTS = POINTS.slice(0, 4);
const POINT_COLOR = { shoulder:"#ffd166", offShoulder:"#f78c6b", elbow:"#ef476f", wrist:"#06d6a0", ball:"#ff6ec7" };
const POINT_LABEL = { shoulder:"throwing shoulder", offShoulder:"off shoulder", elbow:"elbow", wrist:"wrist", ball:"ball" };
const PREFIX = { shoulder:"shoulder", offShoulder:"off_shoulder", elbow:"elbow", wrist:"wrist", ball:"ball" };
const el = id => document.getElementById(id);
const canvas = el("stage"), ctx = canvas.getContext("2d");
const keyOf = pitch => `${pitch.gamePk}_${pitch.playId}`;
const blocked = () => state.busy || state.loading;
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
    method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)
  });
  const result = await response.json();
  if (!response.ok || result.error || (body !== undefined && result.ok !== true))
    throw new Error(result.error || "Request failed. Please try again.");
  return result;
}
function clearPoints() {
  state.pointQuality = "unreviewed"; clearPrediction();
  state.points = {}; state.origin = {}; state.active = null; state.drag = null;
  state.placingPoint = false; state.ballOccluded = false; state.pickingPitcher = false; state.zoomView = null;
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
function markDirty() { clearPrediction(); state.dirty = true; el("saveState").textContent = "Unsaved changes"; }
function visiblePitches() {
  return state.pitches.filter(p => {
    const row = state.labels.get(keyOf(p)), filter = el("queueFilter").value;
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
function renderWorkflow() {
  const points = state.mode === "points", review = points && state.reviewing, locked = blocked();
  el("releaseStep").className = !points ? "active" : "done";
  el("pointsStep").className = review ? "done" : points ? "active" : "";
  el("reviewStep").className = review ? "active" : "";
  el("stepNumber").textContent = `STEP ${review ? 3 : points ? 2 : 1} OF 3`;
  el("stepTitle").textContent = review ? (ANALYSIS ? "Your angle estimate" : "Review & save") : points ? "Mark five points" : "Choose the release";
  el("stepHelp").textContent = review
    ? (ANALYSIS ? "Review the estimate and your marked frame. Save when you’re ready." : "Check the points on the image, then save this pitch to the training dataset.")
    : points ? "Click each point on the image. Drag any point to adjust it, or select its name to place it again."
    : "Use the timeline and neighboring frames to choose the closest available release. An exact, clean frame is not required.";
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
  el("newLocalPitch").hidden = !ANALYSIS || !state.current?.isLocal || !review;
  el("truthDetails").hidden = ANALYSIS || !review;
  if (ANALYSIS || !review) { el("sanityWarn").hidden = true; el("pDelta").textContent = "—"; }
  el("resultCard").hidden = !ANALYSIS;
  el("predictAngle").hidden = !ANALYSIS;
  el("savePoints").textContent = ANALYSIS ? "Save analysis" : "Save & next pitch →";
  if (ANALYSIS && review && !state.ballOccluded && (state.predictionBusy || state.predictedAngle == null)) el("savePoints").disabled = true;
  el("predictAngle").disabled = locked || state.poseBusy || state.predictionBusy || !points || state.ballOccluded || !POINTS.every(n => state.points[n]);
  el("downloadResult").disabled = state.predictedAngle == null;
  if (state.current) {
    el("downloadRelease").href = frameUrl(state.frame) + "&download=1";
    const time = timeAt(state.frame);
    el("frameNum").textContent = `${time == null ? "time unknown" : time.toFixed(3)+" s"} · frame ${state.frame} / ${state.current.nFrames-1}${points ? " · locked" : ""}`;
    el("viewerTitle").textContent = state.current.pitcherName || state.current.sourceName || "Pitch preview";
  }
  el("viewerHint").textContent = review ? "Review the marked frame. Choose Adjust points to make changes." : points ? "The release frame is locked. Place all points on this same image." : "← / → step one frame · Space plays slowly · Click a thumbnail to compare.";
  renderPointButtons();
  if (review) renderReview();
}
function renderReview() {
  const time = timeAt(state.frame);
  const measured = state.ballOccluded ? null : pixelAngle("ball");
  el("reviewBallAngle").textContent = measured == null ? "Unavailable" : `${measured.toFixed(1)}°`;
  el("reviewBallNote").textContent = measured == null ? "Mark the ball to measure this angle." : "From your marked points, relative to horizontal in this image.";
  el("reviewSummary").replaceChildren();
  for (const [label,value] of [["Release",time == null ? `Frame ${state.frame}` : `${time.toFixed(3)} s · frame ${state.frame}`],
      ["Points",state.ballOccluded ? "4 body points · ball hidden" : `${POINTS.filter(n => state.points[n]).length} of 5 placed`],
      ["Timing",{unreviewed:"Not reviewed",closest_available:"Closest available",unclear:"Unclear"}[state.releaseQuality]],
      ["Clarity",{unreviewed:"Not reviewed",reviewed:"Reviewed",uncertain:"Uncertain"}[state.pointQuality]]]) {
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
function frameUrl(frame) {
  return `/api/frame?dir=${encodeURIComponent(state.current.dirName)}&frame=${frame}`;
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
    for (const offset of [-2,-1,1,2]) {
      const frame = state.frame + offset;
      if (frame >= 0 && frame < state.current.nFrames) imageFor(frameUrl(frame)).catch(() => {});
    }
    return true;
  } catch (error) { if (token === state.imageToken) showError(error.message); return false; }
  finally { if (token === state.imageToken) { state.loading = false; renderWorkflow(); } }
}
async function showPitch(index = state.index) {
  stopPlayback(); stashDraft(); state.index = index; state.viewToken++; state.poseToken++; state.poseBusy = false;
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
    if (state.mode === "points" && state.img) {
      if (POINTS.some(n => state.points[n])) fitZoom();

    }
    renderThumbs();
    const next = state.pitches[index+1];
    if (next) fetch(`/api/prefetch?dir=${encodeURIComponent(next.dirName)}`).catch(() => {});
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
    image.src = frameUrl(frame); image.alt = `Frame ${frame}`;
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
function fitZoom() { state.zoomView = null; state.zoom = true; state.zoomView = viewTransform(); draw(); }
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
  renderWorkflow(); draw();
  if (POINTS.some(n => state.points[n])) fitZoom();
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
    if (stage === "release") { state.mode = "points"; renderWorkflow(); draw(); }
    toast(stage === "release" ? "All releases selected. Use “Need points” to finish labeling." : "Queue complete. Add another pitch or choose one to review.");
  }
}
function changeRelease() {
  if (blocked() || state.poseBusy) return;
  clearPrediction(); state.reviewing = false; stopPlayback(); state.mode = "release"; state.zoom = false; state.zoomView = null;
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
    state.pitches.push(data.pitch); el("queueFilter").value = "local";
    el("importPanel").hidden = true; state.busy = false;
    await showPitch(state.pitches.length-1);
  } catch (error) { el("importError").textContent = error.message; el("importError").hidden = false; showError(error.message); }
  finally { state.busy = false; el("importSubmit").disabled = false; renderWorkflow(); }
}
function viewTransform() {
  if (state.zoom && state.zoomView) return state.zoomView;
  const image = state.img;
  if (!image) return { sx:0, sy:0, sw:1, sh:1 };
  const placed = POINTS.map((n) => state.points[n]).filter(Boolean);
  if (!state.zoom || placed.length < 2) {
    return { sx:0, sy:0, sw:image.width, sh:image.height };
  }
  // Frame every placed point, biased toward the wrist->ball end where the
  // clicking is most precision-sensitive.
  const xs = placed.map((p) => p.x), ys = placed.map((p) => p.y);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const span = Math.max(Math.max(...xs) - Math.min(...xs),
                        Math.max(...ys) - Math.min(...ys));
  const half = Math.max(span * 0.85, 110);
  const aspect = image.width / image.height;
  let sw = Math.min(half * 2 * aspect, image.width);
  let sh = Math.min(half * 2, image.height);
  return {
    sx: Math.max(0, Math.min(cx - sw / 2, image.width - sw)),
    sy: Math.max(0, Math.min(cy - sh / 2, image.height - sh)),
    sw, sh
  };
}

function draw() {
  const image = state.img;
  if (!image || !image.width) return;
  const view = viewTransform();
  canvas.width = image.width; canvas.height = image.height;
  ctx.drawImage(image, view.sx, view.sy, view.sw, view.sh, 0, 0, canvas.width, canvas.height);

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
  if (ANALYSIS || !state.reviewing || angle === null || truth === null) {
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
  el("pTruth").textContent = pitch.armAngle != null ? `${Number(pitch.armAngle).toFixed(1)}°` : "Unavailable";
}

function renderProgress() {
  const pitches = visiblePitches();
  const done = pitches.filter(p => isComplete(state.labels.get(keyOf(p)))).length;
  el("progress").textContent = `${done} / ${pitches.length} ${ANALYSIS ? "saved" : "complete"}`;
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
  if (blocked() || state.poseBusy || state.reviewing || state.mode !== "points" || !state.img) return;
  const point = eventToImage(event), handle = state.placingPoint ? null : nearestHandle(point);
  if (handle) { state.drag = handle; canvas.setPointerCapture(event.pointerId); return; }
  const target = state.active || nextEmptyPoint();
  if (!target) return;
  state.placingPoint = false; state.points[target] = point; state.origin[target] = "hand"; state.active = nextEmptyPoint(); markDirty(); draw();
  if (target === "shoulder" && state.pickingPitcher) { state.pickingPitcher = false; poseSeed(); }
});
canvas.addEventListener("pointermove", event => {
  if (!state.drag || blocked() || state.poseBusy || state.reviewing) return;
  const point = eventToImage(event);
  point.x = Math.max(0,Math.min(state.img.width-0.01,point.x)); point.y = Math.max(0,Math.min(state.img.height-0.01,point.y));
  state.points[state.drag] = point; state.origin[state.drag] = "hand"; markDirty(); draw();
});
for (const event of ["pointerup","pointercancel","lostpointercapture"]) canvas.addEventListener(event, () => { state.drag = null; });
el("scrub").addEventListener("input", event => goToFrame(Number(event.target.value)));
el("playPause").addEventListener("click", togglePlay);
el("confirmRelease").addEventListener("click", () => confirmRelease());
el("releaseNext").addEventListener("click", () => confirmRelease({next:true}));
el("savePoints").addEventListener("click", () => saveCurrent());
el("skipPitch").addEventListener("click", () => saveCurrent("skipped"));
el("undoSave").addEventListener("click", undo);
el("seedPoints").addEventListener("click", () => poseSeed());
el("changeRelease").addEventListener("click", changeRelease);
el("zoomPoints").addEventListener("click", () => { if (state.zoom) { state.zoom = false; draw(); } else fitZoom(); });
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
  const pitches = visiblePitches();
  if (pitches.length && !pitches.includes(state.current)) showPitch(state.pitches.indexOf(pitches[0]));
  else { renderQueue(); renderProgress(); if (!pitches.length) toast("No pitches in this queue."); }
});
el("openImport").addEventListener("click", () => { stopPlayback(); el("importPanel").hidden = false; el("videoPath").focus(); });
el("closeImport").addEventListener("click", () => { el("importPanel").hidden = true; });
el("importPanel").addEventListener("submit", event => {
  event.preventDefault(); el("importError").hidden = true;
  addLocalVideo({path:el("videoPath").value.trim(), pitcherName:el("importPitcher").value.trim(), park:el("importPark").value.trim(), pThrows:el("importThrows").value});
});
el("newLocalPitch").addEventListener("click", () => {
  const p = state.current;
  if (state.dirty) { showError("Save the release or point labels before adding another pitch from this video."); return; }
  addLocalVideo({path:p.sourcePath, pitcherName:p.pitcherName, park:p.park, pThrows:p.pThrows, startFrame:Math.min(state.frame+1,p.nFrames-1)});
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
  if (key === "enter") { state.mode === "release" ? confirmRelease() : state.reviewing ? saveCurrent() : goReview(); return; }
  if (key === "u") { undo(); return; }
  if (key === "s") { saveCurrent("skipped"); return; }
  if (state.mode !== "points" || state.poseBusy || state.reviewing) return;
  if (key === "p" && !ANALYSIS) poseSeed({force:event.shiftKey});
  if (key >= "1" && key <= "5") replacePoint(POINTS[Number(key)-1]);
  if (key === "z") el("zoomPoints").click();
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
  document.title = ANALYSIS ? "Arm Angle · Pitch analysis" : "Arm Angle · Training labeler";
  el("analysisNav").className = ANALYSIS ? "active" : "";
  el("trainingNav").className = ANALYSIS ? "" : "active";
  el("workspaceBadge").textContent = ANALYSIS ? "YOUR FOOTAGE" : "DATASET WORKSPACE";
  el("workspaceEyebrow").textContent = ANALYSIS ? "UNDERSTAND YOUR DELIVERY" : "BUILD THE DATASET";
  el("workspaceTitle").textContent = ANALYSIS ? "Pitch analysis" : "Training labeler";
  el("workspaceDescription").textContent = ANALYSIS ? "Open footage, mark the release and five points, then review your estimate." : "One pitch at a time. Choose the release, mark the points, then review and save.";
  const options = ANALYSIS ? [["local","My footage"],["batch","MLB examples"],["all","All available clips"]]
    : [["batch","New MLB trial batch"],["release","Need release"],["points","Need points"],["all","All MLB pitches"]];
  el("queueFilter").replaceChildren();
  for (const [value,label] of options) { const o = document.createElement("option");o.value = value;o.textContent = label;el("queueFilter").append(o); }
  el("queueFilter").value = ANALYSIS ? "local" : (state.pitches.some(p => p.uiTestBatch) ? "batch" : "release");
}
el("prevFrame").addEventListener("click", () => goToFrame(state.frame-1));
el("nextFrame").addEventListener("click", () => goToFrame(state.frame+1));
el("reviewPoints").addEventListener("click", goReview);
el("editPoints").addEventListener("click", editPoints);
el("startVideo").addEventListener("click", () => el("openImport").click());
el("startLocal").addEventListener("click", () => { el("queueFilter").value = "local"; const p = state.pitches.find(p => p.isLocal); if (p) showPitch(state.pitches.indexOf(p)); });
el("startExample").addEventListener("click", () => { el("queueFilter").value = "batch"; const p = state.pitches.find(p => p.uiTestBatch); if (p) showPitch(state.pitches.indexOf(p)); });
el("downloadResult").addEventListener("click", () => {
  if (state.predictedAngle == null) return;
  const p = state.current;
  const text = `Arm Angle — pitch analysis\nPitcher: ${p.pitcherName || "Not specified"}\nVideo: ${p.sourceName || "MLB example"}\nRelease: ${timeAt(state.frame)?.toFixed(3)} s (frame ${state.frame})\nEstimated arm angle: ${state.predictedAngle.toFixed(1)}°\nRelease choice: ${state.releaseQuality}\nPoint clarity: ${state.pointQuality}\n\nMLB-trained estimate. Accuracy on college footage is unverified.\n`;
  const url = URL.createObjectURL(new Blob([text],{type:"text/plain"}));const a = document.createElement("a");a.href = url;a.download = `pitch-${state.frame}-analysis.txt`;a.click();setTimeout(() => URL.revokeObjectURL(url),1000);
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
      el("startLocal").disabled = !state.pitches.some(p => p.isLocal);
      el("startExample").disabled = !state.pitches.some(p => p.uiTestBatch);
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

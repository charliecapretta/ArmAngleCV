"use strict";
for (const button of document.querySelectorAll("[data-delete-session]")) {
  button.addEventListener("click", async () => {
    if (!confirm("Delete this session, including its video and all saved pitches? This cannot be undone.")) return;
    button.disabled = true;
    try {
      const response = await fetch("/api/delete-session", {method:"POST", headers:{"Content-Type":"application/json", "X-CSRF-Token":document.querySelector('meta[name="csrf-token"]').content}, body:JSON.stringify({sessionId:button.dataset.deleteSession})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Deletion failed.");
      button.closest('section').remove();
      document.getElementById("accountStatus").textContent = data.storageDeletionPending
        ? "Session is no longer accessible. Stored file deletion is pending and will be retried."
        : "Session deleted. Existing backup copies age out on their retention schedule.";
    } catch (error) { document.getElementById("accountStatus").textContent = error.message; button.disabled = false; }
  });
}
document.getElementById("deleteAll")?.addEventListener("click", async event => {
  if (!confirm("Delete all your uploads and saved pitches, and end this browser session? This cannot be undone.")) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/delete-all", {method:"POST", headers:{"Content-Type":"application/json", "X-CSRF-Token":document.querySelector('meta[name="csrf-token"]').content}, body:"{}"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Deletion failed.");
    // Replace the page so the old workspace and its controls are not reused.
    location.replace("/privacy");
  } catch (error) {
    document.getElementById("accountStatus").textContent = error.message;
    button.disabled = false;
  }
});

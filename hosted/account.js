"use strict";
for (const button of document.querySelectorAll("[data-delete-session]")) {
  button.addEventListener("click", async () => {
    if (!confirm("Delete this session, including its video and all saved pitches? This cannot be undone.")) return;
    button.disabled = true;
    try {
      const response = await fetch("/api/delete-session", {method:"POST", headers:{"Content-Type":"application/json", "X-CSRF-Token":document.querySelector('meta[name="csrf-token"]').content}, body:JSON.stringify({sessionId:button.dataset.deleteSession})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Deletion failed.");
      location.reload();
    } catch (error) { document.getElementById("accountStatus").textContent = error.message; button.disabled = false; }
  });
}

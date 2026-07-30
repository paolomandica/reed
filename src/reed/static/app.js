// ==========================================================================
// reed — web interface logic
// ==========================================================================

// ---- State ----------------------------------------------------------------
let state = "idle";
let currentTaskId = null;
let debugMode = false;

// ---- DOM refs -------------------------------------------------------------
const sourceToggle     = document.getElementById("source-toggle");
const fileSection      = document.getElementById("file-section");
const pasteSection     = document.getElementById("paste-section");
const dropzone         = document.getElementById("dropzone");
const fileInput        = document.getElementById("file-input");
const fileError        = document.getElementById("file-error");
const fileChip         = document.getElementById("file-chip");
const fileChipName     = document.getElementById("file-chip-name");
const fileChipSize     = document.getElementById("file-chip-size");
const fileChipRemove   = document.getElementById("file-chip-remove");
const pasteInput       = document.getElementById("paste-input");
const pasteError       = document.getElementById("paste-error");
const formatCards      = document.getElementById("format-cards");
const formatInput      = document.getElementById("format-input");
const audiobookOptions = document.getElementById("audiobook-options");
const voiceCards       = document.getElementById("voice-cards");
const voiceInput       = document.getElementById("voice-input");
const voiceHint        = document.getElementById("voice-hint");
const testModeGroup    = document.getElementById("test-mode-group");
const testModeCheck    = document.getElementById("test-mode-check");
const speedPills       = document.getElementById("speed-pills");
const speedInput       = document.getElementById("speed-input");
const btnGenerate      = document.getElementById("btn-generate");
const btnStop          = document.getElementById("btn-stop");
const statusArea       = document.getElementById("status-area");
const statusIcon       = document.getElementById("status-icon");
const statusText       = document.getElementById("status-text");
const progressFill     = document.getElementById("progress-fill");
const progressPct      = document.getElementById("progress-pct");
const progressLabel    = document.getElementById("progress-label");
const waveform         = document.getElementById("waveform");
const resultArea       = document.getElementById("result-area");
const resultFilename   = document.getElementById("result-filename");
const resultDownload   = document.getElementById("result-download");
const errorArea        = document.getElementById("error-area");
const errorMessage     = document.getElementById("error-message");

// ---- Debug config ---------------------------------------------------------
async function fetchConfig() {
  try {
    const res = await fetch("/api/config");
    if (!res.ok) return;
    const cfg = await res.json();
    debugMode = cfg.debug || false;
    testModeGroup.classList.toggle("hidden", !debugMode);
    if (debugMode) loadAllVoices();
  } catch (_) {}
}

// In debug mode, replace the 3 curated voice cards with every Kokoro voice
// so all 20 can be auditioned to pick favourites for the normal-mode UI.
async function loadAllVoices() {
  try {
    const res = await fetch("/api/models");
    if (!res.ok) return;
    const { catalog } = await res.json();
    if (!Array.isArray(catalog) || !catalog.length) return;
    renderVoiceCards(catalog);
  } catch (_) {}
}

function renderVoiceCards(catalog) {
  const current = voiceInput.value;
  const hasCurrent = catalog.some(v => v.id === current);
  voiceCards.classList.add("debug");
  voiceCards.innerHTML = catalog.map((v, i) => {
    const icon = v.gender === "male" ? "👨" : "👩";
    // Fall back to the first voice if the current selection isn't listed.
    const selected = hasCurrent ? v.id === current : i === 0;
    if (selected) voiceInput.value = v.id;
    const grade = v.grade ? `<div class="voice-grade">Grade ${v.grade}</div>` : "";
    return `
      <div class="voice-card${selected ? " selected" : ""}" data-voice="${v.id}"
           role="radio" aria-checked="${selected}" tabindex="0">
        <button type="button" class="voice-preview" data-voice="${v.id}"
                aria-label="Preview voice ${v.id}" title="Preview this voice">▶</button>
        <span class="voice-icon" aria-hidden="true">${icon}</span>
        <div class="voice-desc">${v.id}</div>
        ${grade}
      </div>`;
  }).join("");
  if (voiceHint) {
    voiceHint.textContent =
      `Debug mode — all ${catalog.length} Kokoro voices. Tap ▶ to audition each and note your top 3.`;
  }
}

fetchConfig();

// ---- Helpers --------------------------------------------------------------
function hideAllAreas() {
  statusArea.classList.add("hidden");
  resultArea.classList.add("hidden");
  errorArea.classList.add("hidden");
}

function getSourceType() {
  return sourceToggle.querySelector("button.active").dataset.source;
}

function getFormat() {
  return formatInput.value;
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function setFormEnabled(enabled) {
  sourceToggle.querySelectorAll("button").forEach(b => b.disabled = !enabled);
  fileInput.disabled  = !enabled;
  pasteInput.disabled = !enabled;
  dropzone.style.pointerEvents = enabled ? "auto" : "none";
  dropzone.style.opacity = enabled ? "1" : "0.6";
  voiceCards.querySelectorAll(".voice-card").forEach(c => {
    c.style.pointerEvents = enabled ? "auto" : "none";
    c.style.opacity = enabled ? "1" : "0.6";
  });
  speedPills.querySelectorAll(".speed-pill").forEach(p => { p.disabled = !enabled; });
  formatCards.querySelectorAll(".format-card").forEach(c => {
    c.style.pointerEvents = enabled ? "auto" : "none";
    c.style.opacity = enabled ? "1" : "0.6";
  });
  btnGenerate.disabled = !enabled;
  if (enabled) {
    btnGenerate.textContent = "✨ Create my file";
    btnStop.classList.add("hidden");
  } else {
    btnStop.classList.remove("hidden");
  }
}

function clearFieldErrors() {
  fileError.classList.remove("visible");
  pasteError.classList.remove("visible");
}

// ---- File chip ------------------------------------------------------------
function showFileChip(file) {
  fileChipName.textContent = file.name;
  fileChipSize.textContent = formatBytes(file.size);
  fileChip.classList.add("visible");
  dropzone.classList.add("hidden");
}

function clearFileChip() {
  fileInput.value = "";
  fileChip.classList.remove("visible");
  dropzone.classList.remove("hidden");
}

// ---- Transitions ----------------------------------------------------------
function showStatus(msg, icon) {
  hideAllAreas();
  progressFill.style.width = "0%";
  progressPct.textContent = "0%";
  progressLabel.textContent = "";
  statusIcon.textContent = icon || "📖";
  statusText.textContent = msg;
  const isAudio = getFormat() === "audiobook";
  waveform.classList.toggle("hidden", !isAudio);
  statusArea.classList.remove("hidden");
}

function showResult(filename) {
  hideAllAreas();
  resultFilename.textContent = filename;
  resultArea.classList.remove("hidden");
}

function showError(msg) {
  hideAllAreas();
  errorMessage.textContent = msg;
  errorArea.classList.remove("hidden");
}

function resetForm() {
  hideAllAreas();
  clearFieldErrors();
  stopPreview();
  setFormEnabled(true);
  voiceCards.querySelector(".selected")?.classList.remove("selected");
  voiceCards.querySelector('[data-voice="af_heart"]')?.classList.add("selected");
  voiceInput.value = "af_heart";
  speedPills.querySelector(".active")?.classList.remove("active");
  speedPills.querySelector('[data-speed="1.00"]')?.classList.add("active");
  speedInput.value = "1.00";
  currentTaskId = null;
  btnStop.textContent = "Cancel generation";
  btnStop.disabled = false;
  state = "idle";
}

// ---- Validation -----------------------------------------------------------
function validate() {
  clearFieldErrors();
  let valid = true;
  const sourceType = getSourceType();

  if (sourceType === "file") {
    const file = fileInput.files[0];
    if (!file) {
      fileError.textContent = "Please select an article file to upload.";
      fileError.classList.add("visible");
      valid = false;
    } else {
      const fname = file.name.toLowerCase();
      if (!fname.endsWith(".html") && !fname.endsWith(".htm") && !fname.endsWith(".md")) {
        fileError.textContent = "This file type isn't supported. Please upload an HTML or Markdown file.";
        fileError.classList.add("visible");
        valid = false;
      }
    }
  } else {
    const text = pasteInput.value.trim();
    if (!text) {
      pasteError.textContent = "Please paste some article text first.";
      pasteError.classList.add("visible");
      valid = false;
    }
  }

  return valid;
}

// ---- Format card selection ------------------------------------------------
formatCards.addEventListener("click", (e) => {
  const card = e.target.closest(".format-card");
  if (!card || card.classList.contains("selected")) return;

  formatCards.querySelectorAll(".format-card").forEach(c => {
    c.classList.remove("selected");
    c.setAttribute("aria-checked", "false");
  });
  card.classList.add("selected");
  card.setAttribute("aria-checked", "true");
  formatInput.value = card.dataset.format;

  const isAudiobook = card.dataset.format === "audiobook";
  audiobookOptions.classList.toggle("visible", isAudiobook);
});

// ---- Voice card selection -------------------------------------------------
voiceCards.addEventListener("click", (e) => {
  // Preview buttons live inside the cards — handle them first so tapping ▶
  // auditions a voice without changing the selection.
  const previewBtn = e.target.closest(".voice-preview");
  if (previewBtn) {
    togglePreview(previewBtn);
    return;
  }

  const card = e.target.closest(".voice-card");
  if (!card || card.classList.contains("selected")) return;

  voiceCards.querySelectorAll(".voice-card").forEach(c => {
    c.classList.remove("selected");
    c.setAttribute("aria-checked", "false");
  });
  card.classList.add("selected");
  card.setAttribute("aria-checked", "true");
  voiceInput.value = card.dataset.voice;
});

// ---- Speed pill selection -------------------------------------------------
speedPills.addEventListener("click", (e) => {
  const pill = e.target.closest(".speed-pill");
  if (!pill || pill.classList.contains("active") || pill.disabled) return;

  speedPills.querySelectorAll(".speed-pill").forEach(p => p.classList.remove("active"));
  pill.classList.add("active");
  speedInput.value = pill.dataset.speed;
});

// ---- Voice preview --------------------------------------------------------
const previewAudio = new Audio();
let activePreviewBtn = null;
// Bumped on every start/stop; media events from a superseded clip carry a
// stale token and are ignored (clearing src can fire a late `error` event).
let previewToken = 0;

function resetPreviewBtn(btn) {
  if (!btn) return;
  btn.classList.remove("loading", "playing");
  btn.textContent = "▶";
}

function stopPreview() {
  previewToken++;
  previewAudio.pause();
  previewAudio.removeAttribute("src");
  resetPreviewBtn(activePreviewBtn);
  activePreviewBtn = null;
}

function togglePreview(btn) {
  // Clicking the button that's already playing/loading stops it.
  if (btn === activePreviewBtn) {
    stopPreview();
    return;
  }

  stopPreview();
  const token = ++previewToken;
  activePreviewBtn = btn;
  btn.classList.add("loading");

  const voice = btn.dataset.voice;
  const speed = speedInput.value;
  previewAudio.src = `/api/preview?voice=${encodeURIComponent(voice)}&speed=${encodeURIComponent(speed)}`;
  previewAudio.play().catch(() => {
    // play() rejects if the fetch failed or the tab blocked autoplay.
    if (token !== previewToken) return;
    resetPreviewBtn(btn);
    activePreviewBtn = null;
  });
}

previewAudio.addEventListener("playing", () => {
  if (!activePreviewBtn) return;
  activePreviewBtn.classList.remove("loading");
  activePreviewBtn.classList.add("playing");
  activePreviewBtn.textContent = "❚❚";
});
previewAudio.addEventListener("ended", stopPreview);
previewAudio.addEventListener("error", () => {
  resetPreviewBtn(activePreviewBtn);
  activePreviewBtn = null;
});

// Keyboard activation for card-style radio controls
function enableCardKeyboard(container, selector) {
  container.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const card = e.target.closest(selector);
    if (!card) return;
    e.preventDefault();
    card.click();
  });
}
enableCardKeyboard(formatCards, ".format-card");
enableCardKeyboard(voiceCards, ".voice-card");

// ---- Drag & drop ----------------------------------------------------------
function handleFiles(files) {
  if (!files || !files.length) return;
  // Assign the dropped file to the input so FormData picks it up.
  const dt = new DataTransfer();
  dt.items.add(files[0]);
  fileInput.files = dt.files;
  clearFieldErrors();
  showFileChip(files[0]);
}

fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) {
    clearFieldErrors();
    showFileChip(fileInput.files[0]);
  }
});

fileChipRemove.addEventListener("click", (e) => {
  e.stopPropagation();
  clearFileChip();
});

["dragenter", "dragover"].forEach(evt => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
});
["dragleave", "dragend"].forEach(evt => {
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    if (evt === "dragleave" && dropzone.contains(e.relatedTarget)) return;
    dropzone.classList.remove("dragover");
  });
});
dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  handleFiles(e.dataTransfer.files);
});
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

// ---- Generate -------------------------------------------------------------
async function doGenerate() {
  if (state !== "idle") return;
  if (!validate()) return;

  state = "generating";
  stopPreview();
  clearFieldErrors();
  setFormEnabled(false);

  const fmt = getFormat();
  const statusMsgs = {
    epub:      { text: "Creating your EPUB…", icon: "📖" },
    audiobook: { text: "Narrating your audiobook…", icon: "🎧" },
    markdown:  { text: "Creating your Markdown…", icon: "📝" },
  };
  const msg = statusMsgs[fmt] || statusMsgs.epub;
  showStatus(msg.text, msg.icon);

  const formData = new FormData();
  formData.append("source_type", getSourceType());
  formData.append("format", fmt);

  if (getSourceType() === "file") {
    formData.append("file", fileInput.files[0]);
  } else {
    formData.append("text", pasteInput.value);
  }

  if (fmt === "audiobook") {
    formData.append("speed", speedInput.value);
    formData.append("voice", voiceInput.value);
    if (debugMode && testModeCheck.checked) {
      formData.append("max_chunks", "4");
    }
  }

  try {
    const startRes = await fetch("/api/generate", {
      method: "POST",
      body: formData,
    });

    if (!startRes.ok) {
      let errMsg = `Server returned ${startRes.status}`;
      try {
        const body = await startRes.json();
        if (body && body.error) errMsg = body.error;
      } catch (_) {
        try { errMsg = await startRes.text(); } catch (__) {}
      }
      showError(errMsg);
      setFormEnabled(true);
      state = "idle";
      return;
    }

    const { task_id } = await startRes.json();
    currentTaskId = task_id;

    let pollCount = 0;
    const maxPolls = 600;
    while (pollCount < maxPolls) {
      await new Promise(r => setTimeout(r, 1000));
      pollCount++;

      const pollRes = await fetch(`/api/task/${task_id}`);
      if (!pollRes.ok) {
        showError("Lost connection to the server. Is reed still running?");
        setFormEnabled(true);
        currentTaskId = null;
        state = "idle";
        return;
      }

      const task = await pollRes.json();

      if (task.status === "error") {
        showError(task.error || "Something went wrong during generation.");
        setFormEnabled(true);
        currentTaskId = null;
        state = "idle";
        return;
      }

      if (task.status === "cancelled") {
        setFormEnabled(true);
        currentTaskId = null;
        state = "idle";
        return;
      }

      // Update progress
      progressFill.style.width = task.progress + "%";
      progressPct.textContent = task.progress + "%";
      progressLabel.textContent = task.message || "";

      if (task.status === "done") {
        window.location.href = task.download_url;
        state = "done";
        currentTaskId = null;
        const fmtNames = { epub: "EPUB", audiobook: "Audiobook", markdown: "Markdown" };
        const fmtName = fmtNames[fmt] || "File";
        showResult(fmtName + " — your download should start automatically");
        return;
      }
    }

    showError("This is taking longer than expected. Please try again with a shorter article.");
    setFormEnabled(true);
    currentTaskId = null;
    state = "idle";
  } catch (err) {
    console.error("Fetch error:", err);
    showError("Couldn't reach the server. Make sure reed web is running.");
    setFormEnabled(true);
    currentTaskId = null;
    state = "idle";
  }
}

// ---- Event listeners ------------------------------------------------------
sourceToggle.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn || btn.classList.contains("active")) return;

  sourceToggle.querySelectorAll("button").forEach(b => {
    b.classList.remove("active");
    b.setAttribute("aria-selected", "false");
  });
  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");

  const source = btn.dataset.source;
  fileSection.classList.toggle("visible", source === "file");
  pasteSection.classList.toggle("visible", source === "paste");
  clearFieldErrors();

  if (source === "file") {
    pasteInput.value = "";
  } else {
    clearFileChip();
  }
});

btnGenerate.addEventListener("click", doGenerate);

btnStop.addEventListener("click", async () => {
  if (!currentTaskId) return;
  btnStop.disabled = true;
  btnStop.textContent = "Stopping…";
  try {
    await fetch(`/api/task/${currentTaskId}/stop`, { method: "POST" });
  } catch (_) {}
});

document.getElementById("btn-convert-another").addEventListener("click", resetForm);
document.getElementById("btn-retry").addEventListener("click", resetForm);

// ---- Init -----------------------------------------------------------------
// Audiobook options hidden by default (EPUB is default selection)
audiobookOptions.classList.remove("visible");

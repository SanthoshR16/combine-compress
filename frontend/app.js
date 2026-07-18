/* PDF Tool — all frontend logic in one file */
"use strict";

const $ = (s) => document.querySelector(s);
const formatSize = (b) =>
    b < 1024 ? b + " B"
    : b < 1048576 ? (b / 1024).toFixed(1) + " KB"
    : (b / 1048576).toFixed(1) + " MB";

// ──── State ────

let combineFiles = [];   // File[]
let compressFile = null;  // File | null
let dragSrcIdx = null;
let processing = false;

// ──── Tab switching ────

document.querySelectorAll(".tab").forEach((tab) =>
    tab.addEventListener("click", () => {
        if (processing) return;
        const name = tab.dataset.tab;
        document.querySelectorAll(".tab").forEach((t) => {
            t.classList.toggle("active", t.dataset.tab === name);
            t.setAttribute("aria-selected", t.dataset.tab === name);
        });
        document.querySelectorAll(".panel").forEach((p) =>
            p.classList.toggle("active", p.id === name + "-panel")
        );
    })
);

// ──── Dropzone wiring ────

function initDropzone(dropEl, inputEl, onFiles) {
    dropEl.addEventListener("click", () => { if (!processing) inputEl.click(); });

    dropEl.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropEl.classList.add("dragover");
    });
    dropEl.addEventListener("dragleave", () => dropEl.classList.remove("dragover"));
    dropEl.addEventListener("drop", (e) => {
        e.preventDefault();
        dropEl.classList.remove("dragover");
        const files = [...e.dataTransfer.files].filter((f) =>
            f.name.toLowerCase().endsWith(".pdf")
        );
        if (files.length) onFiles(files);
    });

    inputEl.addEventListener("change", () => {
        if (inputEl.files.length) onFiles([...inputEl.files]);
        inputEl.value = "";
    });
}

// ──── Combine: file management ────

function addCombineFiles(files) {
    combineFiles.push(...files);
    renderCombineList();
}

function removeCombineFile(i) {
    combineFiles.splice(i, 1);
    renderCombineList();
}

function moveCombineFile(from, to) {
    if (to < 0 || to >= combineFiles.length) return;
    const [f] = combineFiles.splice(from, 1);
    combineFiles.splice(to, 0, f);
    renderCombineList();
}

function renderCombineList() {
    const list = $("#combine-list");
    $("#combine-btn").disabled = combineFiles.length < 2;

    if (!combineFiles.length) { list.innerHTML = ""; return; }

    list.innerHTML = combineFiles
        .map(
            (f, i) => `
        <div class="file-item" draggable="true" data-idx="${i}">
            <svg class="file-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
            <span class="file-name" title="${f.name}">${f.name}</span>
            <span class="file-size">${formatSize(f.size)}</span>
            <span class="file-actions">
                <button class="file-btn move" data-move="${i},${i - 1}" title="Move up"${i === 0 ? " disabled" : ""}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="18 15 12 9 6 15"/></svg>
                </button>
                <button class="file-btn move" data-move="${i},${i + 1}" title="Move down"${i === combineFiles.length - 1 ? " disabled" : ""}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
                </button>
                <button class="file-btn" data-remove="${i}" title="Remove">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </span>
        </div>`
        )
        .join("");

    // Event delegation for buttons
    list.querySelectorAll("[data-remove]").forEach((btn) =>
        btn.addEventListener("click", () => removeCombineFile(+btn.dataset.remove))
    );
    list.querySelectorAll("[data-move]").forEach((btn) =>
        btn.addEventListener("click", () => {
            const [from, to] = btn.dataset.move.split(",").map(Number);
            moveCombineFile(from, to);
        })
    );

    // Drag-to-reorder
    list.querySelectorAll(".file-item").forEach((item) => {
        item.addEventListener("dragstart", (e) => {
            dragSrcIdx = +item.dataset.idx;
            item.classList.add("dragging");
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", ""); // required for Firefox
        });
        item.addEventListener("dragover", (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "move";
            item.classList.add("drag-over");
        });
        item.addEventListener("dragleave", () => item.classList.remove("drag-over"));
        item.addEventListener("drop", (e) => {
            e.preventDefault();
            e.stopPropagation(); // don't bubble to dropzone
            item.classList.remove("drag-over");
            const to = +item.dataset.idx;
            if (dragSrcIdx !== null && dragSrcIdx !== to) moveCombineFile(dragSrcIdx, to);
            dragSrcIdx = null;
        });
        item.addEventListener("dragend", () => {
            item.classList.remove("dragging");
            dragSrcIdx = null;
        });
    });
}

// ──── Compress: file management ────

function setCompressFile(files) {
    compressFile = files[0] || null;
    if (!compressFile) return;
    $("#compress-filename").textContent = compressFile.name;
    $("#compress-filesize").textContent = formatSize(compressFile.size);
    $("#compress-info").hidden = false;
    $("#compress-levels").hidden = false;
    $("#compress-drop").hidden = true;
    $("#compress-btn").disabled = false;
}

function clearCompressFile() {
    compressFile = null;
    $("#compress-info").hidden = true;
    $("#compress-levels").hidden = true;
    $("#compress-drop").hidden = false;
    $("#compress-btn").disabled = true;
}

// ──── Upload with separate progress stages ────

function upload(url, formData, processingLabel) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", url);

        xhr.upload.onprogress = (e) => {
            if (e.lengthComputable) {
                const pct = Math.round((e.loaded / e.total) * 100);
                setProgress("Uploading… " + pct + "%", pct);
            }
        };

        xhr.upload.onload = () => {
            setProgress(processingLabel, 100);
        };

        xhr.onload = () => {
            try {
                const data = JSON.parse(xhr.responseText);
                if (xhr.status >= 400) reject(data.detail || "Server error");
                else resolve(data);
            } catch {
                reject("Invalid server response");
            }
        };

        xhr.onerror = () => reject("Network error — is the backend running?");
        xhr.ontimeout = () => reject("Request timed out");
        xhr.timeout = 300000; // 5 min
        xhr.send(formData);
    });
}

// ──── Status overlay ────

function showOverlay() {
    processing = true;
    $("#overlay").hidden = false;
    $("#state-progress").hidden = false;
    $("#state-success").hidden = true;
    $("#state-error").hidden = true;
    $("#progress-fill").style.width = "0%";
}

function setProgress(text, pct) {
    $("#status-text").textContent = text;
    if (pct !== undefined) $("#progress-fill").style.width = pct + "%";
}

function showSuccess(html) {
    $("#state-progress").hidden = true;
    $("#state-success").hidden = false;
    $("#size-info").innerHTML = html || "";
}

function showError(msg) {
    $("#state-progress").hidden = true;
    $("#state-error").hidden = false;
    $("#error-text").textContent = msg;
}

function hideOverlay() {
    processing = false;
    $("#overlay").hidden = true;
    $("#progress-fill").style.width = "0%";
}

function resetAll() {
    hideOverlay();
    combineFiles = [];
    renderCombineList();
    clearCompressFile();
}

// ──── Download trigger ────

function triggerDownload(id) {
    const a = document.createElement("a");
    a.href = "/api/download/" + id;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    setTimeout(() => document.body.removeChild(a), 100);
}

// ──── Actions ────

async function handleCombine() {
    if (combineFiles.length < 2 || processing) return;
    showOverlay();

    const fd = new FormData();
    combineFiles.forEach((f) => fd.append("files", f));

    try {
        const data = await upload("/api/combine", fd, "Combining PDFs…");
        triggerDownload(data.id);
        showSuccess(
            "Combined <strong>" + combineFiles.length + " files</strong> · " + formatSize(data.size)
        );
    } catch (err) {
        showError(typeof err === "string" ? err : err.message || "Failed to combine");
    }
}

async function handleCompress() {
    if (!compressFile || processing) return;
    showOverlay();

    const fd = new FormData();
    fd.append("file", compressFile);
    fd.append("level", document.querySelector('input[name="level"]:checked').value);

    try {
        const data = await upload("/api/compress", fd, "Compressing PDF…");
        triggerDownload(data.id);

        let info = formatSize(data.original_size) + " → " + formatSize(data.compressed_size);
        if (data.compressed_size < data.original_size) {
            const pct = Math.round((1 - data.compressed_size / data.original_size) * 100);
            info += '  <span class="reduction">↓ ' + pct + "% smaller</span>";
        } else {
            info += "<br>File was already optimized";
        }
        showSuccess(info);
    } catch (err) {
        showError(typeof err === "string" ? err : err.message || "Failed to compress");
    }
}

// ──── Init ────

initDropzone($("#combine-drop"), $("#combine-input"), addCombineFiles);
initDropzone($("#compress-drop"), $("#compress-input"), setCompressFile);

$("#combine-btn").addEventListener("click", handleCombine);
$("#compress-btn").addEventListener("click", handleCompress);
$("#compress-remove").addEventListener("click", clearCompressFile);
$("#another-btn").addEventListener("click", resetAll);
$("#retry-btn").addEventListener("click", hideOverlay);

import { browseFilesystem } from "../api";
import type { BrowseResponse } from "../types";

export interface PathPickerCallbacks {
  onOpen: (path: string) => Promise<void>;
}

export function createPathPicker(
  container: HTMLElement,
  callbacks: PathPickerCallbacks,
): { setPath: (path: string) => void; setStatus: (msg: string, isError?: boolean) => void } {
  container.innerHTML = `
    <div class="path-picker">
      <input type="text" id="path-input" class="path-input" placeholder="/path/to/project (created if missing)" spellcheck="false" />
      <button type="button" id="browse-btn" class="btn" title="Browse folders">Browse…</button>
      <button type="button" id="open-btn" class="btn btn-primary">Open</button>
      <span id="path-status" class="path-status"></span>
    </div>
  `;

  const input = container.querySelector<HTMLInputElement>("#path-input")!;
  const browseBtn = container.querySelector<HTMLButtonElement>("#browse-btn")!;
  const btn = container.querySelector<HTMLButtonElement>("#open-btn")!;
  const status = container.querySelector<HTMLSpanElement>("#path-status")!;

  let modal: HTMLElement | null = null;
  let selectedBrowsePath: string | null = null;
  let opening = false;

  function setStatus(msg: string, isError = false) {
    status.textContent = msg;
    status.className = "path-status" + (isError ? " error" : "");
  }

  function setPath(path: string) {
    input.value = path;
    selectedBrowsePath = path;
  }

  function setOpening(active: boolean) {
    opening = active;
    btn.disabled = active;
    browseBtn.disabled = active;
    btn.textContent = active ? "Opening…" : "Open";
  }

  async function doOpen(pathOverride?: string) {
    const path = (pathOverride ?? input.value).trim();
    if (!path) {
      setStatus("Enter or pick a project folder", true);
      return;
    }
    if (opening) return;

    input.value = path;
    selectedBrowsePath = path;
    setOpening(true);
    setStatus("Opening project…");
    try {
      await callbacks.onOpen(path);
      setStatus("Opened");
      window.setTimeout(() => setStatus(""), 2000);
    } catch (e) {
      setStatus(e instanceof Error ? e.message : "Failed to open", true);
    } finally {
      setOpening(false);
    }
  }

  function closeModal() {
    modal?.remove();
    modal = null;
  }

  function escapeHtml(text: string): string {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  }

  function renderBrowseView(
    data: BrowseResponse,
    listEl: HTMLElement,
    pathEl: HTMLElement,
    onSelectionChange: () => void,
    onOpenPath: (path: string) => void,
  ) {
    if (data.path) {
      selectedBrowsePath = data.path;
      input.value = data.path;
    }
    pathEl.textContent = data.path || "Choose a starting location";
    onSelectionChange();

    listEl.innerHTML = "";
    if (data.parent) {
      const up = document.createElement("button");
      up.type = "button";
      up.className = "browse-item browse-up";
      up.innerHTML = `<span>Parent folder</span>`;
      up.addEventListener("click", () =>
        void loadBrowse(data.parent!, listEl, pathEl, onSelectionChange, onOpenPath),
      );
      listEl.appendChild(up);
    }

    if (data.path) {
      const openHere = document.createElement("button");
      openHere.type = "button";
      openHere.className = "browse-item browse-open-here";
      openHere.innerHTML = `<span>Open this folder</span>`;
      openHere.addEventListener("click", () => {
        closeModal();
        void doOpen(data.path!);
      });
      listEl.appendChild(openHere);
    }

    if (!data.entries.length) {
      const empty = document.createElement("div");
      empty.className = "browse-empty";
      empty.textContent = data.path ? "No subfolders — use Open this folder above" : "No locations available";
      listEl.appendChild(empty);
      return;
    }

    for (const entry of data.entries) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "browse-item" + (entry.path === selectedBrowsePath ? " selected" : "");
      row.innerHTML = `<span class="browse-name">${escapeHtml(entry.name)}</span>`;
      row.title = `${entry.path}\nClick to open folder · Double-click to open project`;

      let clickTimer: ReturnType<typeof setTimeout> | null = null;

      row.addEventListener("click", () => {
        if (clickTimer) clearTimeout(clickTimer);
        clickTimer = setTimeout(() => {
          clickTimer = null;
          void loadBrowse(entry.path, listEl, pathEl, onSelectionChange, onOpenPath);
        }, 220);
      });

      row.addEventListener("dblclick", (e) => {
        e.preventDefault();
        if (clickTimer) {
          clearTimeout(clickTimer);
          clickTimer = null;
        }
        closeModal();
        void doOpen(entry.path);
      });

      listEl.appendChild(row);
    }
  }

  async function loadBrowse(
    path: string | undefined,
    listEl: HTMLElement,
    pathEl: HTMLElement,
    onSelectionChange: () => void,
    onOpenPath: (path: string) => void,
  ) {
    listEl.innerHTML = '<div class="browse-loading">Loading…</div>';
    try {
      const data = await browseFilesystem(path);
      renderBrowseView(data, listEl, pathEl, onSelectionChange, onOpenPath);
    } catch (e) {
      listEl.innerHTML = `<div class="browse-error">${escapeHtml(e instanceof Error ? e.message : "Failed to load")}</div>`;
    }
  }

  function openBrowseModal() {
    closeModal();
    modal = document.createElement("div");
    modal.className = "browse-modal-overlay";
    modal.innerHTML = `
      <div class="browse-modal" role="dialog" aria-labelledby="browse-title">
        <div class="browse-modal-header">
          <h2 id="browse-title">Open Folder</h2>
          <button type="button" class="browse-close" aria-label="Close">×</button>
        </div>
        <div class="browse-hint">Click a folder to browse inside it, or double-click to open it as your project.</div>
        <div class="browse-current-path" id="browse-current-path"></div>
        <div class="browse-list" id="browse-list"></div>
        <div class="browse-modal-footer">
          <button type="button" class="btn" id="browse-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" id="browse-select" disabled>Open Project</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    const listEl = modal.querySelector<HTMLElement>("#browse-list")!;
    const pathEl = modal.querySelector<HTMLElement>("#browse-current-path")!;
    const selectBtn = modal.querySelector<HTMLButtonElement>("#browse-select")!;

    const updateSelectState = () => {
      selectBtn.disabled = !selectedBrowsePath || opening;
    };

    const onOpenPath = (path: string) => {
      closeModal();
      void doOpen(path);
    };

    modal.querySelector(".browse-close")!.addEventListener("click", closeModal);
    modal.querySelector("#browse-cancel")!.addEventListener("click", closeModal);
    modal.addEventListener("click", (e) => {
      if (e.target === modal) closeModal();
    });

    selectBtn.addEventListener("click", () => {
      if (selectedBrowsePath) onOpenPath(selectedBrowsePath);
    });

    void loadBrowse(input.value.trim() || undefined, listEl, pathEl, updateSelectState, onOpenPath);
  }

  btn.addEventListener("click", () => void doOpen());
  browseBtn.addEventListener("click", openBrowseModal);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") void doOpen();
  });

  return { setPath, setStatus };
}

import "./layout.css";
import { ensureTerminal, fetchGitDiff, fetchWorkspaceTree, openWorkspace } from "./api";
import { createChatPanel } from "./components/chat";
import { createFileTree } from "./components/fileTree";
import { createPathPicker } from "./components/pathPicker";
import { createTerminal } from "./components/terminal";
import type { GitDiffResponse } from "./types";

const app = document.getElementById("app")!;
app.innerHTML = `
  <div class="bg-mesh" aria-hidden="true"></div>
  <header class="header">
    <div class="logo">
      <div class="logo-icon">λ</div>
      <span class="logo-text">Coding Agent</span>
      <span class="logo-badge">IDE</span>
    </div>
      <div id="path-picker-host" class="path-picker-host"></div>
    <div class="header-right">
      <div class="header-stat">Tokens <span class="val" id="tokens">0</span></div>
      <button class="btn btn-stop" id="stop-btn">■ Stop</button>
      <button class="btn" id="new-chat-btn">New</button>
      <button class="btn" id="delete-chat-btn">Delete</button>
    </div>
  </header>

  <div class="ide-layout">
    <aside class="sidebar">
      <div class="sidebar-header">Chats</div>
      <div class="conv-list" id="conv-list"></div>
      <div class="sidebar-header">Files</div>
      <div class="workspace-info" id="workspace-info">No workspace open</div>
      <div class="file-tree" id="file-tree"></div>
    </aside>

    <main class="main-panel">
      <div class="chat-workspace">
        <div class="chat-column">
          <div class="agent-progress" id="agent-progress" aria-hidden="true">
            <div class="progress-meta">
              <span class="progress-label">Working</span>
              <span class="progress-steps"></span>
            </div>
            <div class="progress-track"><div class="progress-fill"></div></div>
          </div>
          <div class="chat-messages" id="chat-messages"></div>
        </div>
        <aside class="activity-dock" id="activity-dock" aria-hidden="true">
          <div class="dock-header">
            <span class="dock-pulse"></span>
            <span class="dock-title">Agent activity</span>
            <span class="dock-count" id="dock-count"></span>
          </div>
          <div class="dock-body" id="activity-timeline"></div>
        </aside>
      </div>
      <div class="input-bar">
        <div class="composer-toolbar">
          <div class="mode-pills" aria-label="Composer mode">
            <button type="button" class="mode-pill active" data-composer-mode="ask">Ask</button>
            <button type="button" class="mode-pill" data-composer-mode="work">Work</button>
            <button type="button" class="mode-pill" data-composer-mode="debug">Debug</button>
          </div>
          <span class="composer-status" id="composer-status">Ready</span>
        </div>
        <div class="input-wrapper">
          <textarea id="user-input" placeholder="Ask me anything about your code…" rows="1"></textarea>
          <button class="send-btn" id="send-btn">↑</button>
        </div>
      </div>
    </main>

    <aside class="detail-panel" id="detail-panel">
      <div class="detail-header">
        <div class="inspector-tabs">
          <button type="button" class="inspector-tab active" id="file-tab">File</button>
          <button type="button" class="inspector-tab" id="changes-tab">Changes</button>
        </div>
        <span class="detail-title" id="detail-title"></span>
        <button class="detail-close" id="detail-close">×</button>
      </div>
      <div class="detail-body" id="detail-body"></div>
    </aside>
  </div>

  <div class="terminal-panel" id="terminal-panel">
    <div class="terminal-header">
      <span id="terminal-label">Terminal</span>
      <div class="terminal-actions">
        <button class="btn btn-sm" id="terminal-fit">Fit</button>
        <button class="btn btn-sm" id="terminal-toggle">Open</button>
      </div>
    </div>
    <div class="terminal-container" id="terminal-container"></div>
  </div>
`;

const pathPickerHost = document.getElementById("path-picker-host")!;
const fileTreeEl = document.getElementById("file-tree")!;
const chatMessagesEl = document.getElementById("chat-messages")!;
const userInput = document.getElementById("user-input") as HTMLTextAreaElement;
const stopBtn = document.getElementById("stop-btn")!;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;
const tokensEl = document.getElementById("tokens")!;
const composerStatus = document.getElementById("composer-status")!;
const convListEl = document.getElementById("conv-list")!;
const workspaceInfoEl = document.getElementById("workspace-info")!;
const detailPanel = document.getElementById("detail-panel")!;
const detailTitle = document.getElementById("detail-title")!;
const detailBody = document.getElementById("detail-body")!;
const terminalContainer = document.getElementById("terminal-container")!;
const terminalPanel = document.getElementById("terminal-panel")!;
const terminalToggle = document.getElementById("terminal-toggle")!;
const terminalLabel = document.getElementById("terminal-label")!;
const fileTab = document.getElementById("file-tab")!;
const changesTab = document.getElementById("changes-tab")!;

type InspectorMode = "file" | "changes";

let activeInspector: InspectorMode | null = null;
let currentWorkspace = "";
let currentTerminalWs = "";
let terminalOpen = localStorage.getItem("terminal-open") === "true";
let lastFile: { path: string; content: string } | null = null;

function showFile(path: string, content: string) {
  lastFile = { path, content };
  activeInspector = "file";
  setInspectorTab("file");
  detailPanel.classList.add("open");
  detailTitle.textContent = path;
  detailBody.innerHTML = "";
  const lines = content.split("\n");
  lines.forEach((line, i) => {
    const row = document.createElement("div");
    row.className = "code-line ctx";
    row.innerHTML = `<span class="ln">${i + 1}</span><span class="lc">${escapeHtml(line)}</span>`;
    detailBody.appendChild(row);
  });
}

function hasWorkspaceChanges(data: GitDiffResponse): boolean {
  return data.files.length > 0 || Boolean((data.diff || "").trim());
}

async function showChanges(existingData?: GitDiffResponse) {
  if (!currentWorkspace) return;
  activeInspector = "changes";
  setInspectorTab("changes");
  detailPanel.classList.add("open");
  detailTitle.textContent = "Workspace changes";
  detailBody.innerHTML = '<div class="inspector-empty">Loading changes…</div>';
  try {
    const data = existingData ?? (await fetchGitDiff(currentWorkspace));
    renderChanges(data);
  } catch (e) {
    detailBody.innerHTML = `<div class="inspector-empty error">${escapeHtml(e instanceof Error ? e.message : "Failed to load changes")}</div>`;
  }
}

async function openChangesIfPresent() {
  if (!currentWorkspace) return;
  try {
    const data = await fetchGitDiff(currentWorkspace);
    if (hasWorkspaceChanges(data)) {
      await showChanges(data);
    }
  } catch (e) {
    console.error("Failed to load changes", e);
  }
}

function renderChanges(data: GitDiffResponse) {
  detailBody.innerHTML = "";
  const summary = document.createElement("div");
  summary.className = "changes-summary";

  if (!hasWorkspaceChanges(data)) {
    summary.innerHTML = '<div class="inspector-empty">No workspace changes</div>';
    detailBody.appendChild(summary);
    return;
  }

  for (const file of data.files) {
    const row = document.createElement("div");
    row.className = "changed-file";
    row.innerHTML = `<span class="change-status">${escapeHtml(file.status)}</span><span>${escapeHtml(file.path)}</span>`;
    summary.appendChild(row);
  }
  detailBody.appendChild(summary);

  const pre = document.createElement("div");
  pre.className = "diff-view";
  const lines = (data.diff || "(no unstaged diff)").split("\n");
  lines.forEach((line) => {
    const row = document.createElement("div");
    row.className =
      "diff-line" +
      (line.startsWith("+") && !line.startsWith("+++") ? " add" : "") +
      (line.startsWith("-") && !line.startsWith("---") ? " del" : "") +
      (line.startsWith("@@") ? " hunk" : "");
    row.textContent = line || " ";
    pre.appendChild(row);
  });
  detailBody.appendChild(pre);
}

function setInspectorTab(mode: InspectorMode) {
  fileTab.classList.toggle("active", mode === "file");
  changesTab.classList.toggle("active", mode === "changes");
}

function escapeHtml(text: string): string {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

const fileTree = createFileTree(fileTreeEl, {
  onFileSelect: (path, content) => showFile(path, content),
});

const terminal = createTerminal(terminalContainer, {
  onBeforeReconnect: async () => {
    if (!currentWorkspace) return;
    const data = await ensureTerminal(currentWorkspace);
    currentTerminalWs = data.terminal_ws;
  },
});

async function refreshFileTree() {
  if (!currentWorkspace) return;
  try {
    const { tree, total_files } = await fetchWorkspaceTree(currentWorkspace);
    fileTree.render(currentWorkspace, tree);
    workspaceInfoEl.textContent = `${total_files} files · ${currentWorkspace}`;
  } catch (e) {
    console.error("Failed to refresh file tree", e);
  }
}

const chat = createChatPanel(chatMessagesEl, userInput, {
  onFileEdited: (path, content) => {
    fileTree.markEdited(path);
    showFile(path, content);
  },
  onFilesChanged: () => void refreshFileTree(),
  onChangesRequested: () => void openChangesIfPresent(),
  onStatus: (status) => {
    composerStatus.textContent = status;
    const working = /thinking|working|reconnect|finishing/i.test(status);
    composerStatus.classList.toggle("working", working);
  },
  onTokens: (total) => {
    tokensEl.textContent = total.toLocaleString();
  },
});

chat.setConvListEl(convListEl);
chat.bindSend(stopBtn, sendBtn);
chat.bindWelcome();

stopBtn.addEventListener("click", () => chat.stop());
document.getElementById("new-chat-btn")!.addEventListener("click", () => void chat.newChat());
document.getElementById("delete-chat-btn")!.addEventListener("click", () => void chat.deleteCurrentChat());
document.getElementById("detail-close")!.addEventListener("click", () => {
  detailPanel.classList.remove("open");
  activeInspector = null;
});
fileTab.addEventListener("click", () => {
  if (activeInspector === "file" && lastFile) return;
  if (lastFile) {
    showFile(lastFile.path, lastFile.content);
  } else {
    activeInspector = "file";
    setInspectorTab("file");
    detailPanel.classList.add("open");
    detailTitle.textContent = "File";
    detailBody.innerHTML = '<div class="inspector-empty">Select a file from the sidebar.</div>';
  }
});
changesTab.addEventListener("click", () => void showChanges());
document.getElementById("terminal-fit")!.addEventListener("click", () => terminal.fit());
terminalToggle.addEventListener("click", () => setTerminalOpen(!terminalOpen));

function setTerminalOpen(open: boolean) {
  terminalOpen = open;
  terminalPanel.classList.toggle("open", open);
  terminalToggle.textContent = open ? "Close" : "Open";
  localStorage.setItem("terminal-open", String(open));
  if (open) {
    if (currentTerminalWs) terminal.connect(currentTerminalWs);
    window.setTimeout(() => terminal.fit(), 50);
  }
}

async function handleWorkspaceOpen(path: string) {
  const data = await openWorkspace(path);
  currentWorkspace = data.workspace;
  workspaceInfoEl.textContent = `${data.total_files} files · ${data.workspace}`;
  terminalLabel.textContent = `Terminal · ${data.workspace.split("/").filter(Boolean).pop() || data.workspace}`;
  fileTree.render(data.workspace, data.tree);
  chat.setConversation(data.conversation_id, data.workspace);
  chat.setWorkspaceInfo(data.total_files);
  currentTerminalWs = data.terminal_ws;
  terminal.disconnect(false);
  if (terminalOpen) {
    terminal.connect(currentTerminalWs, { reset: true });
  }
  pathPicker.setPath(data.workspace);
}

const pathPicker = createPathPicker(pathPickerHost, {
  onOpen: handleWorkspaceOpen,
});

// Auto-open last workspace on load
const lastWorkspace = localStorage.getItem("last-workspace");
if (lastWorkspace) {
  handleWorkspaceOpen(lastWorkspace).catch((e) => {
    pathPicker.setStatus(e instanceof Error ? e.message : "Failed to open last project", true);
    chatMessagesEl.innerHTML = `
      <div class="welcome">
        <div class="welcome-icon">λ</div>
        <div class="welcome-title">Welcome back</div>
        <div class="welcome-text">Could not open the last project. Pick a folder with Browse… or enter a path, then click Open.</div>
      </div>`;
  });
} else {
  chatMessagesEl.innerHTML = `
    <div class="welcome">
      <div class="welcome-icon">λ</div>
      <div class="welcome-title">Coding Agent</div>
      <div class="welcome-text">Enter a project path above and open it to start.</div>
      <div class="welcome-hint">Your workspace, terminal, and agent chat — all in one place.</div>
    </div>`;
}

setTerminalOpen(terminalOpen);

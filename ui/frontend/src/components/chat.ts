import {
  createConversation,
  deleteConversation as apiDeleteConversation,
  fetchActiveChatRun,
  fetchFile,
  listConversations,
  loadConversation,
  stopAgent,
} from "../api";
import type { ChatEvent } from "../types";
import { renderMarkdown } from "../markdown";

const FILE_EDIT_TOOLS = new Set([
  "write_file",
  "str_replace",
  "insert_at_line",
  "delete_lines",
  "edit_and_verify",
]);

const FILE_MUTATING_TOOLS = new Set([
  ...FILE_EDIT_TOOLS,
  "create_directory",
  "edit_files",
  "search_and_replace_all",
  "generate_test",
]);

const WELCOME_HTML = `
  <div class="welcome" id="welcome">
    <div class="welcome-glow" aria-hidden="true"></div>
    <div class="welcome-hero">
      <div class="welcome-icon">λ</div>
      <h1 class="welcome-title">Coding Agent</h1>
      <p class="welcome-text">Ship faster with an agent that reads, edits, and tests your code.</p>
    </div>
    <div class="welcome-stats" id="welcome-stats">
      <div class="stat-card"><span class="stat-val" data-stat="files">—</span><span class="stat-lbl">Files</span></div>
      <div class="stat-card"><span class="stat-val" data-stat="mode">Ask</span><span class="stat-lbl">Mode</span></div>
      <div class="stat-card accent"><span class="stat-val">Live</span><span class="stat-lbl">Stream</span></div>
    </div>
    <div class="welcome-suggestions">
      <button type="button" class="suggestion-card suggestion" data-mode="work" data-text="Modernize the dashboard UI in templates/index.html with better layout and styling">
        <div class="suggestion-icon">✨</div>
        <div class="suggestion-label">Work</div>
        <div class="suggestion-text">Modernize the dashboard UI in templates/index.html</div>
      </button>
      <button type="button" class="suggestion-card suggestion" data-mode="ask" data-text="Summarize this project structure in 3 bullet points">
        <div class="suggestion-icon">💬</div>
        <div class="suggestion-label">Ask</div>
        <div class="suggestion-text">Summarize this project in 3 bullet points</div>
      </button>
      <button type="button" class="suggestion-card suggestion" data-mode="debug" data-text="Check app.py for bugs or issues">
        <div class="suggestion-icon">🔬</div>
        <div class="suggestion-label">Debug</div>
        <div class="suggestion-text">Check app.py for bugs or issues</div>
      </button>
    </div>
  </div>`;

const PLACEHOLDER_THINKING = "Waiting for model response...";

const ACTIVE_RUN_KEY = "coding-agent-active-run";

type ComposerMode = "ask" | "work" | "debug";

interface ActiveRunState {
  runId: string;
  conversationId: string;
  workspace: string;
  offset: number;
  userMessage: string;
}

export interface ChatPanelCallbacks {
  onFileEdited: (path: string, content: string) => void;
  onFilesChanged?: () => void;
  onChangesRequested?: () => void;
  onStatus?: (status: string) => void;
  onTokens: (total: number) => void;
}

export interface ChatPanel {
  setConversation: (conversationId: string, workspace: string) => void;
  newChat: () => Promise<void>;
  deleteCurrentChat: () => Promise<void>;
  stop: () => void;
  isSending: () => boolean;
  bindSend: (stopBtn: HTMLElement, sendBtn: HTMLButtonElement) => void;
  setConvListEl: (el: HTMLElement) => void;
  refreshConvList: () => Promise<void>;
  bindWelcome: () => void;
  setWorkspaceInfo: (fileCount: number) => void;
}

export function createChatPanel(
  messagesEl: HTMLElement,
  inputEl: HTMLTextAreaElement,
  callbacks: ChatPanelCallbacks,
): ChatPanel {
  let workspace = "";
  let conversationId: string | null = null;
  let sending = false;
  let composerMode: ComposerMode = "ask";
  let activeRun: ActiveRunState | null = null;
  let pendingVisibleMessage: string | null = null;
  let streamConnected = false;
  let reconnectTimer: number | null = null;
  let streamAbort: AbortController | null = null;
  let reconnectListenersBound = false;
  let activityGroup: HTMLElement | null = null;
  let statusPill: HTMLElement | null = null;
  let thinkingTimer: number | null = null;
  let thinkingStart = 0;
  let activityStepCount = 0;
  let llmRound = 0;
  let streamBubbleEl: HTMLElement | null = null;
  let streamMsgEl: HTMLElement | null = null;
  let streamText = "";

  const modePlaceholders: Record<ComposerMode, string> = {
    ask: "Ask about the codebase, files, or next step...",
    work: "Describe the task. I will edit only if it is useful...",
    debug: "Paste an error, failing test, or symptom...",
  };

  function readStoredRun(): ActiveRunState | null {
    try {
      const raw = localStorage.getItem(ACTIVE_RUN_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as ActiveRunState;
      if (!parsed.runId || !parsed.conversationId || !parsed.workspace) return null;
      return {
        ...parsed,
        offset: Number.isFinite(parsed.offset) ? parsed.offset : 0,
      };
    } catch {
      return null;
    }
  }

  function storeActiveRun(run: ActiveRunState | null) {
    activeRun = run;
    if (!run) {
      localStorage.removeItem(ACTIVE_RUN_KEY);
      return;
    }
    localStorage.setItem(ACTIVE_RUN_KEY, JSON.stringify(run));
  }

  function clearActiveRun(runId?: string) {
    if (runId && activeRun?.runId !== runId) return;
    storeActiveRun(null);
    pendingVisibleMessage = null;
  }

  function rememberOffset(data: ChatEvent) {
    if (!activeRun || data.run_id !== activeRun.runId) return;
    if (typeof data.offset !== "number") return;
    if (data.offset + 1 <= activeRun.offset) return;
    storeActiveRun({ ...activeRun, offset: data.offset + 1 });
  }

  function pathArg(args: Record<string, unknown> | undefined): string | null {
    if (!args) return null;
    return (
      (args.filepath as string) ||
      (args.path as string) ||
      (args.file_path as string) ||
      (args.filename as string) ||
      (args.file as string) ||
      null
    );
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function startThinkingTimer(initial = "Thinking") {
    stopThinkingTimer();
    thinkingStart = Date.now();
    updateAgentStatus(initial);
    thinkingTimer = window.setInterval(() => {
      const secs = Math.floor((Date.now() - thinkingStart) / 1000);
      updateAgentStatus(`${initial} · ${secs}s`);
    }, 1000);
  }

  function stopThinkingTimer() {
    if (thinkingTimer) {
      window.clearInterval(thinkingTimer);
      thinkingTimer = null;
    }
  }

  function updateAgentStatus(text: string) {
    callbacks.onStatus?.(text);
    if (!statusPill) {
      statusPill = document.createElement("div");
      statusPill.className = "agent-status";
      statusPill.innerHTML =
        '<span class="status-pulse-wrap"><span class="status-pulse"></span></span><span class="status-text shimmer"></span>';
      messagesEl.appendChild(statusPill);
    }
    const label = statusPill.querySelector(".status-text");
    if (label) label.textContent = text;
    scrollBottom();
  }

  function removeAgentStatus() {
    stopThinkingTimer();
    statusPill?.remove();
    statusPill = null;
  }

  function ensureActivityGroup(): HTMLElement {
    const dock = document.getElementById("activity-dock");
    const timeline = document.getElementById("activity-timeline");
    if (dock) {
      dock.classList.add("active");
      dock.setAttribute("aria-hidden", "false");
    }
    if (!activityGroup && timeline) {
      activityGroup = document.createElement("div");
      activityGroup.className = "activity-group";
      timeline.appendChild(activityGroup);
    }
    if (!activityGroup) {
      activityGroup = document.createElement("div");
      activityGroup.className = "activity-group";
      messagesEl.appendChild(activityGroup);
    }
    return activityGroup;
  }

  function bumpActivityCount() {
    const n = document.querySelectorAll("#activity-timeline .activity-row").length;
    const countEl = document.getElementById("dock-count");
    if (countEl) countEl.textContent = n > 0 ? String(n) : "";
  }

  function hideActivityDock() {
    const dock = document.getElementById("activity-dock");
    dock?.classList.remove("active");
    dock?.setAttribute("aria-hidden", "true");
    const timeline = document.getElementById("activity-timeline");
    if (timeline) timeline.innerHTML = "";
    const countEl = document.getElementById("dock-count");
    if (countEl) countEl.textContent = "";
  }

  function closeActivityGroup() {
    activityGroup = null;
  }

  function resetActivityDock() {
    hideActivityDock();
  }

  function showProgress(label: string, percent: number) {
    const el = document.getElementById("agent-progress");
    if (!el) return;
    el.classList.add("active");
    el.setAttribute("aria-hidden", "false");
    const fill = el.querySelector<HTMLElement>(".progress-fill");
    const lbl = el.querySelector(".progress-label");
    const steps = el.querySelector(".progress-steps");
    if (fill) fill.style.width = `${Math.min(95, Math.max(4, percent))}%`;
    if (lbl) lbl.textContent = label;
    if (steps) {
      const parts: string[] = [];
      if (llmRound > 0) parts.push(`Round ${llmRound}`);
      if (activityStepCount > 0) parts.push(`${activityStepCount} tool${activityStepCount === 1 ? "" : "s"}`);
      steps.textContent = parts.join(" · ");
    }
  }

  function hideProgress() {
    document.getElementById("agent-progress")?.classList.remove("active");
    document.getElementById("agent-progress")?.setAttribute("aria-hidden", "true");
    activityStepCount = 0;
    llmRound = 0;
  }

  function setWorkspaceInfo(fileCount: number) {
    const el = document.querySelector<HTMLElement>('[data-stat="files"]');
    if (el) el.textContent = String(fileCount);
  }

  function formatAgentError(message: string): string {
    if (/timed out|timeout/i.test(message)) {
      return (
        "The model request timed out. Each agent step calls the LLM and can take 30–120+ seconds. " +
        "Set INNKUBE_TIMEOUT=600 in .env for longer runs, or ask a shorter question."
      );
    }
    return message;
  }

  function escapeHtml(text: unknown): string {
    const d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function addBubble(role: string, content: string) {
    const msg = document.createElement("div");
    msg.className = `message ${role}`;
    msg.innerHTML = `<div class="msg-bubble">${renderMarkdown(content)}</div>`;
    messagesEl.appendChild(msg);
    scrollBottom();
  }

  function basename(path: string): string {
    const clean = path.replace(/\\/g, "/");
    return clean.split("/").filter(Boolean).pop() || clean;
  }

  function activityIcon(tool: string | undefined): string {
    if (!tool) return "◆";
    if (tool.includes("read") || tool === "file_outline" || tool === "get_function") return "📄";
    if (tool.includes("search") || tool === "explore_repo") return "🔍";
    if (tool.includes("list") || tool === "view_directory" || tool === "find_files") return "📁";
    if (tool.includes("write") || tool.includes("replace") || tool.includes("edit") || tool.includes("insert")) return "✏️";
    if (tool.includes("run") || tool === "generate_test") return "▶";
    if (tool.startsWith("git_")) return "⎇";
    if (tool.includes("scratchpad")) return "📝";
    return "◆";
  }

  function activityLabel(data: ChatEvent): string {
    const tool = data.tool || "tool";
    const args = data.args || {};
    const path = pathArg(args);
    if (tool === "read_file" && path) return `Read ${path}`;
    if (tool === "read_files") return "Read multiple files";
    if (tool === "view_file_range" && path) return `Read ${path}`;
    if (tool === "file_outline" && path) return `Outlined ${path}`;
    if (tool === "get_function" && path) return `Inspected ${path}`;
    if (tool === "search_code" || tool === "search_and_read") return `Searched "${String(args.query || "").slice(0, 48)}"`;
    if (tool === "search_codebase") return "Searched codebase";
    if (tool === "list_files" || tool === "view_directory" || tool === "explore_repo") return "Explored workspace";
    if (tool === "write_file" && path) return `Edited ${path}`;
    if (tool === "str_replace" && path) return `Edited ${path}`;
    if (tool === "edit_and_verify" && path) return `Edited ${path}`;
    if (tool === "insert_at_line" && path) return `Edited ${path}`;
    if (tool === "delete_lines" && path) return `Edited ${path}`;
    if (tool === "edit_files") return "Edited multiple files";
    if (tool === "search_and_replace_all") return "Edited matching files";
    if (tool === "generate_test") return "Generated reproduction";
    if (tool === "run_tests") return `Ran tests${args.test_path ? `: ${basename(String(args.test_path))}` : ""}`;
    if (tool === "run_code" && path) return `Ran ${path}`;
    if (tool === "run_command") return `Ran ${String(args.command || "command").slice(0, 64)}`;
    if (tool === "git_diff") return "Checked diff";
    if (tool === "git_status") return "Checked status";
    if (tool === "git_log") return "Read git log";
    return tool.replace(/_/g, " ");
  }

  function resetStreamBubble() {
    streamBubbleEl = null;
    streamMsgEl = null;
    streamText = "";
  }

  function beginStreamBubble() {
    if (streamBubbleEl) return;
    removeAgentStatus();
    removeTyping();
    const msg = document.createElement("div");
    msg.className = "message assistant streaming";
    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    const caret = document.createElement("span");
    caret.className = "caret";
    bubble.appendChild(caret);
    msg.appendChild(bubble);
    messagesEl.appendChild(msg);
    streamMsgEl = msg;
    streamBubbleEl = bubble;
    streamText = "";
    scrollBottom();
  }

  function appendStreamToken(chunk: string) {
    if (!chunk) return;
    if (!streamBubbleEl) beginStreamBubble();
    streamText += chunk;
    streamBubbleEl!.innerHTML = renderMarkdown(streamText);
    const caret = document.createElement("span");
    caret.className = "caret";
    streamBubbleEl!.appendChild(caret);
    scrollBottom();
  }

  function discardStreamBubble(content: string) {
    if (streamMsgEl) {
      streamMsgEl.remove();
    }
    resetStreamBubble();
    const text = (content || "").trim();
    if (text) {
      const el = document.createElement("div");
      el.className = "thinking-msg";
      el.textContent = text;
      messagesEl.appendChild(el);
      scrollBottom();
    }
  }

  function endStreamBubble(content: string) {
    const finalText = content || streamText;
    if (streamBubbleEl) {
      streamBubbleEl.innerHTML = renderMarkdown(finalText);
      streamMsgEl?.classList.remove("streaming");
    } else if (finalText) {
      addBubble("assistant", finalText);
    }
    resetStreamBubble();
    scrollBottom();
  }

  function streamBubble(content: string) {
    const msg = document.createElement("div");
    msg.className = "message assistant";
    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    const caret = document.createElement("span");
    caret.className = "caret";
    bubble.appendChild(caret);
    msg.appendChild(bubble);
    messagesEl.appendChild(msg);
    scrollBottom();

    const text = content || "";
    const steps = Math.min(text.length, 240);
    const chunk = Math.max(1, Math.ceil(text.length / steps));
    let i = 0;
    const timer = setInterval(() => {
      i = Math.min(text.length, i + chunk);
      bubble.innerHTML = renderMarkdown(text.slice(0, i));
      if (i < text.length) {
        const c = document.createElement("span");
        c.className = "caret";
        bubble.appendChild(c);
      }
      scrollBottom();
      if (i >= text.length) clearInterval(timer);
    }, 12);
  }

  function summarizeResult(result: string): string {
    const line = result.split("\n").map((l) => l.trim()).find(Boolean) || "";
    if (line.length <= 80) return line;
    return line.slice(0, 77) + "…";
  }

  function addToolCard(data: ChatEvent) {
    const id = "tool-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
    const card = document.createElement("div");
    const result = data.result || "";
    const failed =
      data.denied ||
      result.startsWith("Error") ||
      result.startsWith("[EDIT:FAILED]") ||
      result.startsWith("[EDIT:AMBIGUOUS]");
    const done = !failed && result.length > 0;
    card.className = "activity-row" + (failed ? " failed" : done ? " done" : "");
    card.style.animationDelay = `${Math.min(activityStepCount, 8) * 0.05}s`;
    const time = data.denied ? "denied" : (data.tool_elapsed || 0) + "s";
    const preview = summarizeResult(result);
    const detail = result.length > 500 ? result.slice(0, 500) + "\n…" : result;
    card.innerHTML = `
      <div class="activity-step-row">
        <div class="step-ring">${done ? "✓" : failed ? "!" : "·"}</div>
        <button class="activity-head" type="button" data-tool-id="${id}">
          <span class="activity-icon">${activityIcon(data.tool)}</span>
          <span class="activity-body">
            <span class="activity-label">${escapeHtml(activityLabel(data))}</span>
            <span class="activity-preview">${escapeHtml(preview)}</span>
          </span>
          <span class="activity-time">${time}</span>
          <span class="activity-chevron">›</span>
        </button>
      </div>
      <pre class="activity-detail" id="${id}">${escapeHtml(detail)}</pre>
    `;
    card.querySelector(".activity-head")?.addEventListener("click", () => {
      const detailEl = document.getElementById(id);
      const open = detailEl?.classList.toggle("show");
      card.classList.toggle("expanded", Boolean(open));
    });
    const group = sending ? ensureActivityGroup() : messagesEl;
    group.appendChild(card);
    bumpActivityCount();
    if (!sending) scrollBottom();
  }

  function removeTyping() {
    document.getElementById("typing")?.remove();
  }

  function finishSend(
    stopBtn: HTMLElement,
    sendBtn: HTMLButtonElement,
    status = "Ready",
  ) {
    sending = false;
    sendBtn.disabled = false;
    stopBtn.classList.remove("show");
    removeTyping();
    removeAgentStatus();
    closeActivityGroup();
    hideProgress();
    resetStreamBubble();
    callbacks.onStatus?.(status);
    inputEl.focus();
    if (conversationId) refreshConvList();
  }

  function beginSend(stopBtn: HTMLElement, sendBtn: HTMLButtonElement, status = "Working...") {
    sending = true;
    activityStepCount = 0;
    llmRound = 0;
    resetActivityDock();
    callbacks.onStatus?.(status);
    sendBtn.disabled = true;
    stopBtn.classList.add("show");
    showProgress("Starting…", 6);
    startThinkingTimer("Thinking");
  }

  function toolSucceeded(data: ChatEvent): boolean {
    if (data.denied || !data.tool) return false;
    const result = data.result || "";
    if (result.startsWith("Error") || result.startsWith("[EDIT:FAILED]")) return false;
    if (data.tool === "create_directory" && result.startsWith("Created directory")) return true;
    if (data.tool === "write_file" && result.startsWith("Wrote")) return true;
    if (FILE_EDIT_TOOLS.has(data.tool)) {
      return (
        result.startsWith("[EDIT:OK]") ||
        result.startsWith("Wrote") ||
        result.startsWith("Inserted") ||
        result.startsWith("Deleted") ||
        result.startsWith("Replaced in")
      );
    }
    if (data.tool === "edit_files") return result.includes("[EDIT:OK]");
    if (data.tool === "search_and_replace_all") return result.startsWith("Replaced in");
    if (data.tool === "generate_test") return !result.startsWith("Error");
    return false;
  }

  async function handlePostEdit(data: ChatEvent) {
    const p = pathArg(data.args);
    if (p && data.tool && FILE_EDIT_TOOLS.has(data.tool)) {
      const res = await fetchFile(p, workspace);
      if (res?.content != null) callbacks.onFileEdited(p, res.content);
    }
    if (toolSucceeded(data)) {
      callbacks.onFilesChanged?.();
      callbacks.onChangesRequested?.();
    }
  }

  function handleEvent(
    data: ChatEvent,
    stopBtn: HTMLElement,
    sendBtn: HTMLButtonElement,
  ) {
    rememberOffset(data);
    if (data.type === "run_started") {
      if (data.run_id && conversationId) {
        const visibleMessage = pendingVisibleMessage || data.user_message || "";
        storeActiveRun({
          runId: data.run_id,
          conversationId,
          workspace,
          offset: typeof data.offset === "number" ? data.offset + 1 : 0,
          userMessage: visibleMessage,
        });
        if (!pendingVisibleMessage && visibleMessage) {
          document.getElementById("welcome")?.remove();
          addBubble("user", visibleMessage);
        }
      }
      callbacks.onStatus?.("Working...");
      return;
    }
    if (data.type === "run_finished") {
      clearActiveRun(data.run_id);
      if (typeof data.tokens?.total === "number") {
        callbacks.onTokens(data.tokens.total);
      }
      const status = data.status === "error"
        ? "Error"
        : data.status === "stopped"
          ? "Stopped"
          : "Ready";
      finishSend(stopBtn, sendBtn, status);
      return;
    }
    if (data.type === "error") {
      removeTyping();
      removeAgentStatus();
      closeActivityGroup();
      const errMsg = document.createElement("div");
      errMsg.className = "message assistant";
      errMsg.innerHTML = `<div class="msg-bubble error-bubble">${escapeHtml(formatAgentError(data.message || "An error occurred"))}</div>`;
      messagesEl.appendChild(errMsg);
      if (activeRun) callbacks.onStatus?.("Error");
      else finishSend(stopBtn, sendBtn, "Error");
      return;
    }
    if (data.type === "rag") {
      removeTyping();
      const el = document.createElement("div");
      el.className = "rag-msg";
      el.textContent = "◆ " + (data.info || "");
      messagesEl.appendChild(el);
      startThinkingTimer("Indexing codebase");
      scrollBottom();
      return;
    }
    if (data.type === "stopped") {
      removeTyping();
      removeAgentStatus();
      closeActivityGroup();
      const el = document.createElement("div");
      el.className = "stopped-msg";
      el.textContent = "■ Stopped by you";
      messagesEl.appendChild(el);
      if (activeRun) callbacks.onStatus?.("Stopped");
      else finishSend(stopBtn, sendBtn, "Stopped");
      return;
    }
    if (data.type === "heartbeat") {
      const secs = Math.round(Number(data.elapsed) || 0);
      if (typeof data.round === "number") llmRound = data.round;
      startThinkingTimer(`Thinking · ${secs}s`);
      showProgress(`Thinking · ${secs}s`, 12 + Math.min(50, secs * 2));
      return;
    }
    if (data.type === "status") {
      updateAgentStatus(data.content || "Working…");
      return;
    }
    if (data.type === "tool_call") {
      removeTyping();
      removeAgentStatus();
      activityStepCount += 1;
      addToolCard(data);
      const label = activityLabel(data);
      startThinkingTimer(label);
      showProgress(label, 18 + activityStepCount * 12);
      if (typeof data.tokens?.total === "number") callbacks.onTokens(data.tokens.total);
      if (!data.denied && data.tool && FILE_MUTATING_TOOLS.has(data.tool)) {
        void handlePostEdit(data);
      }
      scrollBottom();
      return;
    }
    if (data.type === "thinking") {
      const content = (data.content || "").trim();
      if (!content || content === PLACEHOLDER_THINKING) {
        startThinkingTimer("Thinking");
        return;
      }
      const preview = content.length > 72 ? content.slice(0, 72) + "…" : content;
      startThinkingTimer(preview);
      return;
    }
    if (data.type === "response_start") {
      beginStreamBubble();
      showProgress("Writing response…", 70);
      return;
    }
    if (data.type === "token") {
      appendStreamToken(data.content || "");
      showProgress("Writing response…", 72 + Math.min(20, streamText.length / 40));
      return;
    }
    if (data.type === "stream_discard") {
      discardStreamBubble(data.content || streamText);
      return;
    }
    if (data.type === "response_end") {
      removeTyping();
      removeAgentStatus();
      closeActivityGroup();
      endStreamBubble(data.content || "");
      if (typeof data.tokens?.total === "number") callbacks.onTokens(data.tokens.total);
      if (activeRun) callbacks.onStatus?.("Finishing...");
      else finishSend(stopBtn, sendBtn);
      return;
    }
    if (data.type === "response") {
      removeTyping();
      removeAgentStatus();
      closeActivityGroup();
      if (streamBubbleEl) {
        endStreamBubble(data.content || "");
      } else {
        streamBubble(data.content || "");
      }
      if (typeof data.tokens?.total === "number") callbacks.onTokens(data.tokens.total);
      if (activeRun) callbacks.onStatus?.("Finishing...");
      else finishSend(stopBtn, sendBtn);
    }
  }

  function replayEvent(data: ChatEvent) {
    if (data.type === "user") addBubble("user", data.content || "");
    else if (data.type === "response") addBubble("assistant", data.content || "");
    else if (data.type === "tool_call") addToolCard(data);
    else if (data.type === "thinking") {
      const content = (data.content || "").trim();
      if (!content || content === PLACEHOLDER_THINKING) return;
      const el = document.createElement("div");
      el.className = "thinking-msg";
      el.textContent = content;
      messagesEl.appendChild(el);
    } else if (data.type === "rag") {
      const el = document.createElement("div");
      el.className = "rag-msg";
      el.textContent = "◆ " + (data.info || "");
      messagesEl.appendChild(el);
    } else if (data.type === "stopped") {
      const el = document.createElement("div");
      el.className = "stopped-msg";
      el.textContent = "■ Stopped by you";
      messagesEl.appendChild(el);
    } else if (data.type === "error") {
      const errMsg = document.createElement("div");
      errMsg.className = "message assistant";
      errMsg.innerHTML = `<div class="msg-bubble error-bubble">${escapeHtml(formatAgentError(data.message || ""))}</div>`;
      messagesEl.appendChild(errMsg);
    }
  }

  let convListEl: HTMLElement | null = null;

  function setConvListEl(el: HTMLElement) {
    convListEl = el;
  }

  function relativeTime(iso?: string): string {
    if (!iso) return "";
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return mins + "m ago";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    return Math.floor(hrs / 24) + "d ago";
  }

  async function refreshConvList() {
    if (!convListEl || !workspace) return;
    const convs = await listConversations(workspace);
    convListEl.innerHTML = "";
    for (const conv of convs) {
      const el = document.createElement("div");
      el.className = "conv-item" + (conv.id === conversationId ? " active" : "");
      el.innerHTML = `
        <div class="conv-body">
          <div class="conv-title">${escapeHtml(conv.title || "New chat")}</div>
          <div class="conv-time">${relativeTime(conv.updated_at)}</div>
        </div>
        <button class="conv-delete" title="Delete">×</button>
      `;
      el.querySelector(".conv-body")?.addEventListener("click", () => {
        void switchConversation(conv.id);
      });
      el.querySelector(".conv-delete")?.addEventListener("click", (e) => {
        e.stopPropagation();
        void deleteConversation(conv.id, conv.title || "this chat");
      });
      convListEl.appendChild(el);
    }
  }

  async function hydrateConversation(id: string) {
    const data = await loadConversation(id, workspace);
    messagesEl.innerHTML = "";
    if (!data?.transcript?.length) {
      messagesEl.innerHTML = WELCOME_HTML;
      bindWelcome();
      return;
    }
    for (const ev of data.transcript) replayEvent(ev);
    scrollBottom();
  }

  async function switchConversation(id: string) {
    if (sending || id === conversationId) return;
    conversationId = id;
    await refreshConvList();
    await hydrateConversation(id);
  }

  async function deleteConversation(id: string, title: string) {
    if (sending) return;
    if (!confirm(`Delete "${title}"? This cannot be undone.`)) return;
    const ok = await apiDeleteConversation(id, workspace);
    if (!ok) {
      alert("Failed to delete conversation.");
      return;
    }
    if (id === conversationId) {
      const convs = await listConversations(workspace);
      if (convs.length > 0) {
        conversationId = convs[0].id;
        await refreshConvList();
        await hydrateConversation(conversationId);
      } else {
        await newChat();
      }
    } else {
      await refreshConvList();
    }
  }

  let stopBtnRef: HTMLElement | null = null;
  let sendBtnRef: HTMLButtonElement | null = null;

  function bindWelcome() {
    messagesEl.querySelectorAll(".suggestion").forEach((el) => {
      el.addEventListener("click", () => {
        const mode = (el as HTMLElement).dataset.mode as ComposerMode | undefined;
        if (mode && mode in modePlaceholders) setComposerMode(mode);
        inputEl.value = (el as HTMLElement).dataset.text || el.textContent || "";
        inputEl.focus();
        if (stopBtnRef && sendBtnRef) sendMessage(stopBtnRef, sendBtnRef);
      });
    });
  }

  function clearReconnectTimer() {
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function scheduleReconnect(stopBtn: HTMLElement, sendBtn: HTMLButtonElement) {
    if (!activeRun || reconnectTimer || streamConnected) return;
    callbacks.onStatus?.("Reconnecting...");
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      void reconnectActiveRun(stopBtn, sendBtn);
    }, document.visibilityState === "visible" ? 1200 : 3000);
  }

  async function streamEvents(
    input: RequestInfo | URL,
    init: RequestInit,
    stopBtn: HTMLElement,
    sendBtn: HTMLButtonElement,
  ) {
    clearReconnectTimer();
    const controller = new AbortController();
    streamAbort = controller;
    streamConnected = true;

    try {
      const response = await fetch(input, { ...init, signal: controller.signal });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ error: response.statusText }));
        if (response.status === 404 && activeRun) {
          clearActiveRun();
          finishSend(stopBtn, sendBtn);
          return;
        }
        throw new Error(err.error || "Chat stream failed");
      }
      const reader = response.body?.getReader();
      if (!reader) throw new Error("Chat stream did not return a body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            handleEvent(JSON.parse(line.slice(6)), stopBtn, sendBtn);
          } catch (e) {
            console.error(e);
          }
        }
      }

      streamConnected = false;
      if (streamAbort === controller) streamAbort = null;
      if (activeRun) {
        scheduleReconnect(stopBtn, sendBtn);
      } else if (sending) {
        finishSend(stopBtn, sendBtn);
      }
    } catch (err) {
      streamConnected = false;
      if (streamAbort === controller) streamAbort = null;
      if ((err as Error).name === "AbortError") return;
      if (activeRun) {
        scheduleReconnect(stopBtn, sendBtn);
        return;
      }
      removeTyping();
      const errMsg = document.createElement("div");
      errMsg.className = "message assistant";
      errMsg.innerHTML = `<div class="msg-bubble error-bubble">Connection error: ${escapeHtml((err as Error).message)}</div>`;
      messagesEl.appendChild(errMsg);
      finishSend(stopBtn, sendBtn, "Connection error");
    }
  }

  async function reconnectActiveRun(stopBtn: HTMLElement, sendBtn: HTMLButtonElement) {
    if (!activeRun || streamConnected) return;
    beginSend(stopBtn, sendBtn, "Reconnecting...");
    const run = activeRun;
    await streamEvents(
      `/api/chat/runs/${encodeURIComponent(run.runId)}/events?offset=${run.offset}`,
      { method: "GET" },
      stopBtn,
      sendBtn,
    );
  }

  function renderResumeUser(run: ActiveRunState) {
    if (!run.userMessage || !document.getElementById("welcome")) return;
    document.getElementById("welcome")?.remove();
    addBubble("user", run.userMessage);
  }

  async function resumeActiveRunIfNeeded() {
    if (!conversationId || !workspace || !stopBtnRef || !sendBtnRef) return;

    const stored = readStoredRun();
    if (stored?.conversationId === conversationId && stored.workspace === workspace) {
      storeActiveRun(stored);
    }

    const serverRun = await fetchActiveChatRun(conversationId).catch(() => null);
    if (!serverRun) {
      if (activeRun?.conversationId === conversationId) clearActiveRun();
      return;
    }

    const shouldReplayRun = Boolean(document.getElementById("welcome"));
    const offset = shouldReplayRun
      ? 0
      : activeRun?.runId === serverRun.id
        ? activeRun.offset
        : 0;
    const nextRun: ActiveRunState = {
      runId: serverRun.id,
      conversationId: serverRun.conversation_id,
      workspace: serverRun.workspace,
      offset,
      userMessage: serverRun.user_message,
    };
    storeActiveRun(nextRun);
    renderResumeUser(nextRun);
    beginSend(stopBtnRef, sendBtnRef, "Reconnecting...");
    await reconnectActiveRun(stopBtnRef, sendBtnRef);
  }

  function sendMessage(stopBtn: HTMLElement, sendBtn: HTMLButtonElement) {
    const msg = inputEl.value.trim();
    if (!msg || sending || !conversationId) return;
    const outbound = buildOutboundMessage(msg);

    document.getElementById("welcome")?.remove();
    addBubble("user", msg);
    inputEl.value = "";
    inputEl.style.height = "42px";

    pendingVisibleMessage = msg;
    beginSend(stopBtn, sendBtn);

    void streamEvents("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: outbound,
        display_message: msg,
        workspace,
        conversation_id: conversationId,
        mode: composerMode,
      }),
    }, stopBtn, sendBtn);
  }

  function setConversation(id: string, ws: string) {
    const stored = readStoredRun();
    conversationId = stored?.workspace === ws ? stored.conversationId : id;
    workspace = ws;
    messagesEl.innerHTML = WELCOME_HTML;
    bindWelcome();
    void refreshConvList();
    void resumeActiveRunIfNeeded();
  }

  async function newChat() {
    if (sending || !workspace) return;
    const created = await createConversation(workspace);
    conversationId = created.id;
    messagesEl.innerHTML = WELCOME_HTML;
    bindWelcome();
    await refreshConvList();
  }

  function buildOutboundMessage(message: string): string {
    if (composerMode === "work") {
      return (
        "Help with this task. For large UI/HTML/CSS changes, read the file once then use write_file with the full updated content. " +
        "Use str_replace only for small surgical edits — old_str must match exactly.\n\nUser request:\n" +
        message
      );
    }
    if (composerMode === "debug") {
      return `Diagnose the issue first. Use errors, tests, and terminal context if relevant. Make a code change only when the cause is clear.\n\nUser request:\n${message}`;
    }
    return message;
  }

  function setComposerMode(mode: ComposerMode) {
    composerMode = mode;
    inputEl.placeholder = modePlaceholders[mode];
    callbacks.onStatus?.("Ready");
    const modeStat = document.querySelector<HTMLElement>('[data-stat="mode"]');
    if (modeStat) modeStat.textContent = mode.charAt(0).toUpperCase() + mode.slice(1);
    document.querySelectorAll("[data-composer-mode]").forEach((el) => {
      el.classList.toggle("active", (el as HTMLElement).dataset.composerMode === mode);
    });
  }

  function bindModeButtons() {
    setComposerMode(composerMode);
    document.querySelectorAll<HTMLButtonElement>("[data-composer-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        const mode = button.dataset.composerMode as ComposerMode | undefined;
        if (!mode || !(mode in modePlaceholders)) return;
        setComposerMode(mode);
        inputEl.focus();
      });
    });
  }

  function bindReconnectListeners() {
    if (reconnectListenersBound) return;
    reconnectListenersBound = true;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && activeRun && stopBtnRef && sendBtnRef) {
        void reconnectActiveRun(stopBtnRef, sendBtnRef);
      }
    });
    window.addEventListener("online", () => {
      if (activeRun && stopBtnRef && sendBtnRef) {
        void reconnectActiveRun(stopBtnRef, sendBtnRef);
      }
    });
  }

  function stopCurrentRun() {
    if (!conversationId) return;
    const runId = activeRun?.runId;
    void stopAgent(conversationId, runId);
    streamAbort?.abort();
    streamAbort = null;
    streamConnected = false;
    clearReconnectTimer();
    clearActiveRun(runId);
    removeTyping();

    if (sending && stopBtnRef && sendBtnRef) {
      const el = document.createElement("div");
      el.className = "stopped-msg";
      el.textContent = "■ Stopped by you";
      messagesEl.appendChild(el);
      scrollBottom();
      finishSend(stopBtnRef, sendBtnRef, "Stopped");
    }
  }

  return {
    setConversation: (id: string, ws: string) => setConversation(id, ws),
    newChat,
    deleteCurrentChat: async () => {
      if (!conversationId) return;
      const active = convListEl?.querySelector(".conv-item.active .conv-title");
      await deleteConversation(conversationId, active?.textContent || "this chat");
    },
    stop: () => {
      stopCurrentRun();
    },
    isSending: () => sending,
    bindSend: (stopBtn: HTMLElement, sendBtn: HTMLButtonElement) => {
      stopBtnRef = stopBtn;
      sendBtnRef = sendBtn;
      bindModeButtons();
      bindReconnectListeners();
      sendBtn.addEventListener("click", () => sendMessage(stopBtn, sendBtn));
      inputEl.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          sendMessage(stopBtn, sendBtn);
        }
      });
      inputEl.addEventListener("input", () => {
        inputEl.style.height = "42px";
        inputEl.style.height = Math.min(inputEl.scrollHeight, 150) + "px";
      });
    },
    setConvListEl,
    refreshConvList,
    bindWelcome,
    setWorkspaceInfo,
  };
}

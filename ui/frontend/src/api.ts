import type {
  BrowseResponse,
  ChatEvent,
  ChatRunInfo,
  ConversationMeta,
  FileContent,
  GitDiffResponse,
  OpenWorkspaceResponse,
  TreeNode,
} from "./types";

const SESSION_KEY = "coding-agent-session-id";

function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
}

export function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = generateId();
    try {
      localStorage.setItem(SESSION_KEY, id);
    } catch {
      // private browsing or storage blocked — use in-memory id for this session
    }
  }
  return id;
}

export async function fetchWorkspaceTree(
  workspace: string,
): Promise<{ tree: TreeNode[]; total_files: number }> {
  const res = await fetch("/api/workspace?workspace=" + encodeURIComponent(workspace));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "Failed to refresh file tree");
  }
  const data = await res.json();
  return { tree: data.tree || [], total_files: data.total_files || 0 };
}

export async function browseFilesystem(path?: string): Promise<BrowseResponse> {
  const url = path
    ? "/api/fs/browse?path=" + encodeURIComponent(path)
    : "/api/fs/browse";
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "Failed to browse");
  }
  return res.json();
}

export async function openWorkspace(path: string): Promise<OpenWorkspaceResponse> {
  const res = await fetch("/api/workspace/open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, session_id: getSessionId() }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "Failed to open workspace");
  }
  return res.json();
}

export async function fetchFile(path: string, workspace: string): Promise<FileContent | null> {
  const url =
    "/api/file?path=" +
    encodeURIComponent(path) +
    "&workspace=" +
    encodeURIComponent(workspace);
  const res = await fetch(url);
  return res.ok ? res.json() : null;
}

export async function fetchGitDiff(workspace: string): Promise<GitDiffResponse> {
  const res = await fetch("/api/git/diff?workspace=" + encodeURIComponent(workspace));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || "Failed to load changes");
  }
  return res.json();
}

export async function fetchTreeChildren(
  workspace: string,
  path: string,
): Promise<TreeNode[]> {
  const url =
    "/api/workspace/tree?workspace=" +
    encodeURIComponent(workspace) +
    "&path=" +
    encodeURIComponent(path);
  const res = await fetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  return data.children || [];
}

export async function listConversations(workspace: string): Promise<ConversationMeta[]> {
  const res = await fetch(
    "/api/conversations?workspace=" + encodeURIComponent(workspace),
  );
  const data = await res.json();
  return data.conversations || [];
}

export async function createConversation(workspace: string): Promise<ConversationMeta> {
  const res = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace }),
  });
  return res.json();
}

export async function loadConversation(
  id: string,
  workspace: string,
): Promise<{ transcript?: ChatEvent[] } | null> {
  const res = await fetch(
    "/api/conversations/" +
      encodeURIComponent(id) +
      "?workspace=" +
      encodeURIComponent(workspace),
  );
  return res.ok ? res.json() : null;
}

export async function fetchActiveChatRun(
  conversationId: string,
): Promise<ChatRunInfo | null> {
  const res = await fetch(
    "/api/chat/runs/active?conversation_id=" + encodeURIComponent(conversationId),
  );
  if (!res.ok) return null;
  const data = await res.json();
  return data.run || null;
}

export async function deleteConversation(id: string, workspace: string): Promise<boolean> {
  const res = await fetch(
    "/api/conversations/" +
      encodeURIComponent(id) +
      "?workspace=" +
      encodeURIComponent(workspace),
    { method: "DELETE" },
  );
  return res.ok;
}

export async function stopAgent(conversationId: string, runId?: string): Promise<void> {
  await fetch("/api/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, run_id: runId }),
  });
}

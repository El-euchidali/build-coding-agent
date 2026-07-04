export interface BrowseEntry {
  name: string;
  path: string;
}

export interface BrowseResponse {
  path: string | null;
  parent: string | null;
  entries: BrowseEntry[];
  roots: boolean;
}

export interface TreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: TreeNode[];
}

export interface OpenWorkspaceResponse {
  workspace: string;
  conversation_id: string;
  tree: TreeNode[];
  terminal_ws: string;
  total_files: number;
}

export interface FileContent {
  path: string;
  content: string;
  lines: number;
}

export interface GitDiffFile {
  path: string;
  status: string;
}

export interface GitDiffResponse {
  files: GitDiffFile[];
  diff: string;
}

export interface ConversationMeta {
  id: string;
  title: string;
  updated_at?: string;
}

export interface ChatRunInfo {
  id: string;
  conversation_id: string;
  workspace: string;
  user_message: string;
  status: string;
  done: boolean;
  event_count: number;
}

export interface ChatEvent {
  type: string;
  run_id?: string;
  offset?: number;
  status?: string;
  done?: boolean;
  user_message?: string;
  conversation_id?: string;
  workspace?: string;
  elapsed?: number;
  round?: number;
  content?: string;
  tool?: string;
  args?: Record<string, unknown>;
  result?: string;
  denied?: boolean;
  tool_elapsed?: number;
  tokens?: { total?: number };
  info?: string;
  message?: string;
}

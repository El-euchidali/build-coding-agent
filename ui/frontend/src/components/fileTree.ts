import { fetchFile, fetchTreeChildren, fetchWorkspaceTree } from "../api";
import type { TreeNode } from "../types";

export interface FileTreeCallbacks {
  onFileSelect: (path: string, content: string) => void;
}

export function createFileTree(
  container: HTMLElement,
  callbacks: FileTreeCallbacks,
): {
  render: (workspace: string, tree: TreeNode[]) => void;
  refresh: () => Promise<void>;
  markEdited: (path: string) => void;
} {
  let workspace = "";
  const editedFiles = new Set<string>();

  function render(workspacePath: string, tree: TreeNode[]) {
    workspace = workspacePath;
    container.innerHTML = "";
    if (!tree.length) {
      container.innerHTML = '<div class="tree-empty">No files found</div>';
      return;
    }
    const ul = document.createElement("ul");
    ul.className = "tree-root";
    for (const node of tree) {
      ul.appendChild(renderNode(node, 0));
    }
    container.appendChild(ul);
  }

  function renderNode(node: TreeNode, depth: number): HTMLElement {
    const li = document.createElement("li");
    li.className = "tree-node";

    const row = document.createElement("div");
    row.className = "tree-row" + (editedFiles.has(node.path) ? " edited" : "");
    row.style.paddingLeft = `${depth * 12 + 8}px`;
    row.dataset.path = node.path;
    row.dataset.isdir = String(node.is_dir);

    const icon = node.is_dir ? "▸" : "·";
    row.innerHTML = `<span class="tree-icon">${icon}</span><span class="tree-label">${escapeHtml(node.name)}</span>`;

    if (node.is_dir) {
      const childrenEl = document.createElement("ul");
      childrenEl.className = "tree-children collapsed";
      let loaded = !!(node.children && node.children.length);

      if (node.children?.length) {
        for (const child of node.children) {
          childrenEl.appendChild(renderNode(child, depth + 1));
        }
      }

      row.addEventListener("click", async (e) => {
        e.stopPropagation();
        const iconEl = row.querySelector(".tree-icon")!;
        if (childrenEl.classList.contains("collapsed")) {
          if (!loaded) {
            const children = await fetchTreeChildren(workspace, node.path);
            for (const child of children) {
              childrenEl.appendChild(renderNode(child, depth + 1));
            }
            loaded = true;
          }
          childrenEl.classList.remove("collapsed");
          iconEl.textContent = "▾";
        } else {
          childrenEl.classList.add("collapsed");
          iconEl.textContent = "▸";
        }
      });
      li.appendChild(row);
      li.appendChild(childrenEl);
    } else {
      row.addEventListener("click", async (e) => {
        e.stopPropagation();
        container.querySelectorAll(".tree-row.active").forEach((el) => el.classList.remove("active"));
        row.classList.add("active");
        const res = await fetchFile(node.path, workspace);
        callbacks.onFileSelect(node.path, res?.content ?? "(could not load file)");
      });
      li.appendChild(row);
    }

    return li;
  }

  async function refresh() {
    if (!workspace) return;
    const { tree } = await fetchWorkspaceTree(workspace);
    render(workspace, tree);
  }

  function markEdited(path: string) {
    editedFiles.add(path);
    container.querySelectorAll(".tree-row").forEach((el) => {
      if ((el as HTMLElement).dataset.path === path) {
        el.classList.add("edited");
      }
    });
  }

  return { render, refresh, markEdited };
}

function escapeHtml(text: string): string {
  const d = document.createElement("div");
  d.textContent = text;
  return d.innerHTML;
}

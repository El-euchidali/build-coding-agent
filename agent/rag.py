"""
RAG (Retrieval-Augmented Generation) for the coding agent.

Indexes a codebase into a vector database at task startup, then lets the agent
retrieve relevant code by semantic search instead of reading files blindly.

This is critical for large repositories (e.g. SWE-bench) where the agent
cannot afford to read thousands of files to find the relevant one.
"""

import ast
import hashlib
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

# Lazy-loaded global embedder (loading the model takes a few seconds)
_embedder: SentenceTransformer | None = None


def _get_embedder() -> SentenceTransformer:
    """Load the embedding model once and reuse it."""
    global _embedder
    if _embedder is None:
        # all-MiniLM-L6-v2: small, fast, 384-dimensional embeddings, runs locally
        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder


def _chunk_by_function(filepath: Path, workspace: Path) -> list[dict]:
    """
    Split a Python file into chunks at function and class boundaries using AST.
    Each chunk is a complete function or class — semantically meaningful.
    Falls back to fixed-size chunks if the file cannot be parsed.
    """
    try:
        code = filepath.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []

    rel_path = str(filepath.relative_to(workspace))
    chunks: list[dict] = []

    try:
        tree = ast.parse(code)
        lines = code.splitlines()

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno - 1
                end = getattr(node, "end_lineno", start + 1)
                chunk_text = "\n".join(lines[start:end])

                # Skip tiny chunks
                if len(chunk_text.strip()) < 20:
                    continue

                chunk_id = hashlib.md5(
                    f"{rel_path}:{start}:{end}".encode()
                ).hexdigest()

                chunks.append({
                    "id": chunk_id,
                    "text": chunk_text,
                    "file": rel_path,
                    "start_line": start + 1,
                    "end_line": end,
                    "name": node.name,
                })

    except SyntaxError:
        # Fall back to fixed-size chunks for unparseable files
        for i in range(0, len(code), 800):
            chunk_text = code[i:i + 800]
            if len(chunk_text.strip()) < 20:
                continue
            chunk_id = hashlib.md5(f"{rel_path}:chunk:{i}".encode()).hexdigest()
            chunks.append({
                "id": chunk_id,
                "text": chunk_text,
                "file": rel_path,
                "start_line": code[:i].count("\n") + 1,
                "end_line": code[:i + 800].count("\n") + 1,
                "name": "(text chunk)",
            })

    return chunks


class CodebaseIndex:
    """
    A semantic index over a codebase. Built once per task, queried many times.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        # In-memory ChromaDB client — fresh per task, no persistence needed
        self.client = chromadb.Client()
        # Unique collection name per workspace to avoid collisions
        coll_name = "codebase_" + hashlib.md5(
            str(workspace).encode()
        ).hexdigest()[:8]
        # Reset if it already exists
        try:
            self.client.delete_collection(coll_name)
        except Exception:
            pass
        self.collection = self.client.create_collection(coll_name)
        self.indexed_chunks = 0

    def build(self, max_files: int = 2000) -> int:
        """
        Index all Python files in the workspace.
        Returns the number of chunks indexed.
        """
        embedder = _get_embedder()

        all_chunks: list[dict] = []
        file_count = 0

        from agent.filesystem import FileSystem
        fs = FileSystem(self.workspace)
        for filepath in fs.tracked_files("*.py"):
            if file_count >= max_files:
                break
            chunks = _chunk_by_function(filepath, self.workspace)
            all_chunks.extend(chunks)
            file_count += 1

        if not all_chunks:
            return 0

        # Embed in batches for efficiency
        texts = [c["text"] for c in all_chunks]
        embeddings = embedder.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
        ).tolist()

        # Add in batches to avoid ChromaDB's max batch size limit
        batch_size = 5000
        for start in range(0, len(all_chunks), batch_size):
            end = min(start + batch_size, len(all_chunks))
            self.collection.add(
                ids=[c["id"] for c in all_chunks[start:end]],
                documents=texts[start:end],
                embeddings=embeddings[start:end],
                metadatas=[
                    {
                        "file": c["file"],
                        "start_line": c["start_line"],
                        "end_line": c["end_line"],
                        "name": c["name"],
                    }
                    for c in all_chunks[start:end]
                ],
            )

        self.indexed_chunks = len(all_chunks)
        return self.indexed_chunks

    def search(self, query: str, n_results: int = 5) -> str:
        """
        Semantic search for relevant code. Returns formatted results with
        file paths and line numbers so the agent can read the exact location.
        """
        if self.indexed_chunks == 0:
            return "Codebase index is empty."

        embedder = _get_embedder()
        query_vec = embedder.encode([query]).tolist()

        results = self.collection.query(
            query_embeddings=query_vec,
            n_results=min(n_results, self.indexed_chunks),
        )

        if not results["documents"] or not results["documents"][0]:
            return f"No relevant code found for: {query}"

        output = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i]
            output.append(
                f"### {meta['file']}:{meta['start_line']}-{meta['end_line']} "
                f"({meta['name']})\n{doc}\n"
            )

        return "\n".join(output)


# Global index for the current task (set by the runner)
_current_index: CodebaseIndex | None = None


def set_current_index(index: CodebaseIndex | None) -> None:
    """Set the active codebase index for the current task."""
    global _current_index
    _current_index = index


def search_codebase(workspace: Path, query: str) -> str:
    """
    Tool function: semantic search over the indexed codebase.
    Used by the agent via the search_codebase tool.
    """
    if _current_index is None:
        return "Codebase index not available. Use search_code for text search instead."
    return _current_index.search(query, n_results=5)
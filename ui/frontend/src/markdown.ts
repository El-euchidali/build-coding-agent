import { marked } from "marked";

marked.setOptions({
  gfm: true,
  breaks: true,
});

marked.use({
  renderer: {
    code({ text, lang }) {
      const language = lang ? ` class="language-${lang}"` : "";
      return `<pre class="code-block"><code${language}>${escapeHtml(text)}</code></pre>`;
    },
  },
});

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderMarkdown(content: string): string {
  if (!content) return "";
  return marked.parse(content, { async: false }) as string;
}

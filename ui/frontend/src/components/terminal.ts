import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

const encoder = new TextEncoder();

export function createTerminal(container: HTMLElement): {
  connect: (wsPath: string, opts?: { reset?: boolean }) => void;
  disconnect: (clearPath?: boolean) => void;
  fit: () => void;
} {
  const term = new Terminal({
    cursorBlink: true,
    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
    fontSize: 13,
    theme: {
      background: "#fafafa",
      foreground: "#1a1a1a",
      cursor: "#1a1a1a",
      selectionBackground: "#c7d2fe",
    },
  });

  const fitAddon = new FitAddon();
  term.loadAddon(fitAddon);
  term.loadAddon(new WebLinksAddon());
  term.open(container);

  let ws: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let currentWsPath = "";
  let dataDisposable: { dispose: () => void } | null = null;
  let shouldReconnect = false;
  let reconnectAttempts = 0;

  // Let Ctrl+C / Ctrl+D / Ctrl+Z reach the PTY instead of the browser.
  term.attachCustomKeyEventHandler((event) => {
    if (event.type !== "keydown") return true;
    if (!event.ctrlKey && !event.metaKey) return true;

    const key = event.key.toLowerCase();
    if (event.ctrlKey && event.key === "c" && term.hasSelection()) {
      return false;
    }
    if (event.ctrlKey && ["c", "d", "z", "\\"].includes(key)) {
      event.preventDefault();
      return true;
    }
    return true;
  });

  function sendInput(data: string) {
    if (ws?.readyState !== WebSocket.OPEN) return;
    ws.send(encoder.encode(data));
  }

  function sendResize() {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    fitAddon.fit();
    ws.send(
      JSON.stringify({
        type: "resize",
        cols: term.cols,
        rows: term.rows,
      }),
    );
  }

  function connect(wsPath: string, opts?: { reset?: boolean }) {
    const forceReset = opts?.reset ?? false;
    shouldReconnect = true;
    if (
      !forceReset &&
      currentWsPath === wsPath &&
      (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    const pathChanged = currentWsPath !== wsPath;
    disconnect(false);
    shouldReconnect = true;
    currentWsPath = wsPath;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}${wsPath}`;

    if (forceReset || pathChanged) {
      term.clear();
      term.writeln("\x1b[33mConnecting to terminal…\x1b[0m");
    }

    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      if (forceReset || pathChanged) {
        term.clear();
      }
      reconnectAttempts = 0;
      fitAddon.fit();
      sendResize();
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(ev.data));
      } else {
        term.write(String(ev.data));
      }
    };

    ws.onclose = () => {
      ws = null;
      if (!shouldReconnect || !currentWsPath) return;
      reconnectAttempts += 1;
      if (reconnectAttempts > 5) {
        shouldReconnect = false;
        term.writeln("\r\n\x1b[33mTerminal disconnected. Open the terminal to reconnect.\x1b[0m");
        return;
      }
      const delay = Math.min(1000 * reconnectAttempts, 5000);
      term.writeln(`\r\n\x1b[33mTerminal disconnected. Reconnecting in ${Math.round(delay / 1000)}s…\x1b[0m`);
      reconnectTimer = window.setTimeout(() => {
        if (currentWsPath) connect(currentWsPath);
      }, delay);
    };

    ws.onerror = () => {
      term.writeln("\r\n\x1b[31mTerminal connection error\x1b[0m");
    };

    dataDisposable = term.onData(sendInput);
  }

  function disconnect(clearPath = true) {
    shouldReconnect = false;
    dataDisposable?.dispose();
    dataDisposable = null;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null;
      ws.close();
      ws = null;
    }
    if (clearPath) currentWsPath = "";
  }

  function fit() {
    fitAddon.fit();
    sendResize();
  }

  const resizeObserver = new ResizeObserver(() => fit());
  resizeObserver.observe(container);
  window.addEventListener("resize", fit);
  container.addEventListener("click", () => term.focus());

  return { connect, disconnect, fit };
}

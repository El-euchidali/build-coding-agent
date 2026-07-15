"""PTY session manager with WebSocket bridge for Xterm.js."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import signal
import struct
import termios
from dataclasses import dataclass
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

TIOCSWINSZ = getattr(termios, "TIOCSWINSZ", 0x5414)
TIOCSCTTY = getattr(termios, "TIOCSCTTY", 0x540E)


@dataclass
class PtySession:
    master_fd: int
    pid: int
    cwd: Path


class PtySessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}

    def _default_shell(self) -> str:
        shell = os.environ.get("SHELL", "/bin/bash")
        return shell if os.path.exists(shell) else "/bin/bash"

    def kill_sync(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return
        try:
            os.close(session.master_fd)
        except OSError:
            pass
        try:
            pgid = os.getpgid(session.pid)
            os.killpg(pgid, signal.SIGHUP)
        except OSError:
            try:
                os.kill(session.pid, signal.SIGHUP)
            except OSError:
                pass
        try:
            os.waitpid(session.pid, os.WNOHANG)
        except ChildProcessError:
            pass

    async def kill(self, session_id: str) -> None:
        self.kill_sync(session_id)

    async def respawn(self, session_id: str, cwd: Path) -> None:
        await asyncio.to_thread(self.respawn_sync, session_id, cwd)

    def respawn_sync(self, session_id: str, cwd: Path) -> None:
        """Spawn a shell in cwd (sync — run via asyncio.to_thread)."""
        self.kill_sync(session_id)

        master_fd, slave_fd = pty.openpty()
        pid = os.fork()
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            # Attach slave as controlling terminal so Ctrl+C / job control work
            try:
                fcntl.ioctl(slave_fd, TIOCSCTTY, 0)
            except (OSError, TypeError):
                try:
                    fcntl.ioctl(slave_fd, TIOCSCTTY, b"")
                except OSError:
                    pass
            os.chdir(cwd)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            shell = self._default_shell()
            os.execvp(shell, [shell, "-i"])
            os._exit(1)

        os.close(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._sessions[session_id] = PtySession(master_fd, pid, cwd)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(session.master_fd, TIOCSWINSZ, winsize)

    def send_signal(self, session_id: str, sig: int) -> None:
        """Send a signal to the terminal process group (e.g. SIGINT for Ctrl+C)."""
        session = self._sessions.get(session_id)
        if not session:
            return
        try:
            os.killpg(os.getpgid(session.pid), sig)
        except OSError:
            try:
                os.kill(session.pid, sig)
            except OSError:
                pass

    async def attach_websocket(self, ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        session = self._sessions.get(session_id)
        if not session:
            await ws.send_json(
                {"type": "info", "message": "Waiting for terminal session..."}
            )
            while not session:
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=1)
                except asyncio.TimeoutError:
                    session = self._sessions.get(session_id)
                    continue
                if message["type"] == "websocket.disconnect":
                    return
                session = self._sessions.get(session_id)

        loop = asyncio.get_running_loop()
        output_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def on_readable() -> None:
            try:
                data = os.read(session.master_fd, 4096)
                if data:
                    output_queue.put_nowait(data)
                else:
                    output_queue.put_nowait(None)
            except BlockingIOError:
                return
            except OSError:
                output_queue.put_nowait(None)

        loop.add_reader(session.master_fd, on_readable)

        async def pump_output() -> None:
            while True:
                data = await output_queue.get()
                if data is None:
                    break
                await ws.send_bytes(data)

        pump_task = asyncio.create_task(pump_output())

        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break

                if message.get("bytes"):
                    payload = message["bytes"]
                    os.write(session.master_fd, payload)
                elif message.get("text"):
                    text = message["text"]
                    try:
                        payload = json.loads(text)
                        if isinstance(payload, dict) and payload.get("type") == "resize":
                            self.resize(
                                session_id,
                                int(payload.get("cols", 80)),
                                int(payload.get("rows", 24)),
                            )
                            continue
                        if isinstance(payload, dict) and payload.get("type") == "signal":
                            sig_name = payload.get("name", "INT")
                            sig = getattr(signal, f"SIG{sig_name}", signal.SIGINT)
                            self.send_signal(session_id, sig)
                            continue
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    os.write(session.master_fd, text.encode("utf-8", errors="surrogateescape"))
        except WebSocketDisconnect:
            pass
        finally:
            loop.remove_reader(session.master_fd)
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass

    async def shutdown(self) -> None:
        for session_id in list(self._sessions.keys()):
            await self.kill(session_id)


pty_manager = PtySessionManager()

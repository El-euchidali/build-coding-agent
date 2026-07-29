"""PTY session manager with WebSocket bridge for Xterm.js.

Unix uses the stdlib ``pty``/``fcntl`` APIs. Windows uses ConPTY via ``pywinpty``.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

IS_WINDOWS = sys.platform == "win32"

if not IS_WINDOWS:
    import fcntl
    import pty
    import struct
    import termios

    TIOCSWINSZ = getattr(termios, "TIOCSWINSZ", 0x5414)
    TIOCSCTTY = getattr(termios, "TIOCSCTTY", 0x540E)
else:
    try:
        from winpty import PtyProcess
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Windows terminal support requires pywinpty. "
            "Install it with: pip install pywinpty"
        ) from exc


@dataclass
class PtySession:
    # Unix: master_fd is an int; Windows: process is a PtyProcess
    master_fd: int | None = None
    pid: int | None = None
    cwd: Path | None = None
    process: object | None = None  # winpty.PtyProcess on Windows


class PtySessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, PtySession] = {}
        # Bumped whenever a WS attaches so an older bridge exits cleanly.
        self._attach_epoch: dict[str, int] = {}

    def _default_shell(self) -> str | list[str]:
        if IS_WINDOWS:
            for candidate in (
                os.path.expandvars(
                    r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
                ),
                os.path.expandvars(r"%SystemRoot%\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"),
            ):
                if os.path.exists(candidate):
                    return [candidate, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass"]
            comspec = os.environ.get("COMSPEC")
            if comspec and os.path.exists(comspec):
                return comspec
            return "cmd.exe"
        shell = os.environ.get("SHELL", "/bin/bash")
        return shell if os.path.exists(shell) else "/bin/bash"

    def _is_alive(self, session: PtySession) -> bool:
        if IS_WINDOWS:
            process = session.process
            try:
                return bool(process is not None and process.isalive())
            except Exception:
                return False
        return session.master_fd is not None and session.pid is not None

    def kill_sync(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        if IS_WINDOWS:
            process = session.process
            if process is None:
                return
            try:
                if process.isalive():
                    process.terminate()
            except Exception:
                pass
            try:
                process.close(force=True)
            except Exception:
                pass
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
        await asyncio.to_thread(self.kill_sync, session_id)

    async def respawn(self, session_id: str, cwd: Path) -> None:
        await asyncio.to_thread(self.respawn_sync, session_id, cwd)

    def respawn_sync(self, session_id: str, cwd: Path) -> None:
        """Spawn a shell in cwd (sync — run via asyncio.to_thread)."""
        self.kill_sync(session_id)

        if IS_WINDOWS:
            shell = self._default_shell()
            process = PtyProcess.spawn(shell, cwd=str(cwd))
            self._sessions[session_id] = PtySession(
                process=process,
                pid=getattr(process, "pid", None),
                cwd=cwd,
            )
            return

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
            assert isinstance(shell, str)
            os.execvp(shell, [shell, "-i"])
            os._exit(1)

        os.close(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._sessions[session_id] = PtySession(master_fd=master_fd, pid=pid, cwd=cwd)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        session = self._sessions.get(session_id)
        if not session or not self._is_alive(session):
            return
        if IS_WINDOWS:
            process = session.process
            if process is None:
                return
            try:
                process.setwinsize(rows, cols)
            except Exception:
                pass
            return
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(session.master_fd, TIOCSWINSZ, winsize)
        except OSError:
            pass

    def send_signal(self, session_id: str, sig: int) -> None:
        """Send a signal to the terminal process group (e.g. SIGINT for Ctrl+C)."""
        session = self._sessions.get(session_id)
        if not session or not self._is_alive(session):
            return
        if IS_WINDOWS:
            process = session.process
            if process is None:
                return
            # ConPTY: Ctrl+C is sent as the interrupt character, not a POSIX signal.
            if sig in (getattr(signal, "SIGINT", 2), 2):
                self._write(session, b"\x03")
            return
        try:
            os.killpg(os.getpgid(session.pid), sig)
        except OSError:
            try:
                os.kill(session.pid, sig)
            except OSError:
                pass

    def _write(self, session: PtySession, data: bytes) -> bool:
        if IS_WINDOWS:
            process = session.process
            if process is None:
                return False
            try:
                process.write(data.decode("utf-8", errors="surrogateescape"))
                return True
            except EOFError:
                return False
            except Exception:
                return False
        try:
            os.write(session.master_fd, data)
            return True
        except OSError:
            return False

    def _ensure_session(self, session_id: str) -> PtySession | None:
        session = self._sessions.get(session_id)
        if session and self._is_alive(session):
            return session
        if session:
            self.kill_sync(session_id)

        # After reload / dead PTY, revive from the open workspace if we have one.
        try:
            from ui.session import get_session_workspace
        except ImportError:
            return None
        workspace = get_session_workspace(session_id)
        if not workspace:
            return None
        try:
            self.respawn_sync(session_id, workspace)
        except Exception:
            return None
        return self._sessions.get(session_id)

    async def attach_websocket(self, ws: WebSocket, session_id: str) -> None:
        await ws.accept()
        session = self._ensure_session(session_id)
        if not session:
            await ws.send_json(
                {"type": "info", "message": "Open a workspace to start the terminal."}
            )
            while not session:
                try:
                    message = await asyncio.wait_for(ws.receive(), timeout=1)
                except asyncio.TimeoutError:
                    session = self._ensure_session(session_id)
                    continue
                if message["type"] == "websocket.disconnect":
                    return
                session = self._ensure_session(session_id)

        epoch = self._attach_epoch.get(session_id, 0) + 1
        self._attach_epoch[session_id] = epoch

        if IS_WINDOWS:
            await self._attach_websocket_windows(ws, session, session_id, epoch)
        else:
            await self._attach_websocket_unix(ws, session, session_id, epoch)

    def _is_current_attach(self, session_id: str, epoch: int) -> bool:
        return self._attach_epoch.get(session_id) == epoch

    async def _attach_websocket_unix(
        self, ws: WebSocket, session: PtySession, session_id: str, epoch: int
    ) -> None:
        loop = asyncio.get_running_loop()
        output_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def on_readable() -> None:
            if not self._is_current_attach(session_id, epoch):
                output_queue.put_nowait(None)
                return
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
        input_task = asyncio.create_task(
            self._websocket_input_loop(ws, session, session_id, epoch)
        )

        try:
            done, pending = await asyncio.wait(
                {pump_task, input_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                try:
                    await task
                except (asyncio.CancelledError, WebSocketDisconnect):
                    pass
                except Exception:
                    pass
        finally:
            try:
                loop.remove_reader(session.master_fd)
            except Exception:
                pass
            if self._is_current_attach(session_id, epoch) and not self._is_alive(session):
                self._sessions.pop(session_id, None)

    async def _attach_websocket_windows(
        self, ws: WebSocket, session: PtySession, session_id: str, epoch: int
    ) -> None:
        process = session.process
        output_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        stop = threading.Event()

        def reader_thread() -> None:
            while not stop.is_set():
                if not self._is_current_attach(session_id, epoch):
                    loop.call_soon_threadsafe(output_queue.put_nowait, None)
                    return
                try:
                    data = process.read(4096)
                except EOFError:
                    loop.call_soon_threadsafe(output_queue.put_nowait, None)
                    return
                except Exception:
                    loop.call_soon_threadsafe(output_queue.put_nowait, None)
                    return
                if not data:
                    if not process.isalive():
                        loop.call_soon_threadsafe(output_queue.put_nowait, None)
                        return
                    continue
                payload = (
                    data.encode("utf-8", errors="surrogateescape")
                    if isinstance(data, str)
                    else data
                )
                loop.call_soon_threadsafe(output_queue.put_nowait, payload)

        thread = threading.Thread(target=reader_thread, daemon=True)
        thread.start()

        async def pump_output() -> None:
            while True:
                data = await output_queue.get()
                if data is None:
                    break
                try:
                    await ws.send_bytes(data)
                except Exception:
                    break

        pump_task = asyncio.create_task(pump_output())
        input_task = asyncio.create_task(
            self._websocket_input_loop(ws, session, session_id, epoch)
        )

        try:
            done, pending = await asyncio.wait(
                {pump_task, input_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                try:
                    await task
                except (asyncio.CancelledError, WebSocketDisconnect):
                    pass
                except Exception:
                    pass
        finally:
            stop.set()
            if self._is_current_attach(session_id, epoch) and not self._is_alive(session):
                self._sessions.pop(session_id, None)

    async def _websocket_input_loop(
        self, ws: WebSocket, session: PtySession, session_id: str, epoch: int
    ) -> None:
        try:
            while self._is_current_attach(session_id, epoch):
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break

                if message.get("bytes"):
                    if not self._write(session, message["bytes"]):
                        break
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
                    if not self._write(
                        session,
                        text.encode("utf-8", errors="surrogateescape"),
                    ):
                        break
        except WebSocketDisconnect:
            pass

    async def shutdown(self) -> None:
        for session_id in list(self._sessions.keys()):
            await self.kill(session_id)


pty_manager = PtySessionManager()

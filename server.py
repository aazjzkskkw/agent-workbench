#!/usr/bin/env python3
"""Agent Desktop Workbench — multi-runtime control plane over the DeepSeek Harness SDK.

One RuntimeManager owns N RuntimeInstance objects, each running its own DeepSeek
Harness runtime subprocess (own sessions, event log, approval bridge, request
thread). The browser talks to all of them over one HTTP + SSE surface.

Stdlib only — no FastAPI/uvicorn/aiohttp required.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent

# DeepSeek Harness checkout: override with $DSH_REPO when the sibling layout
# does not apply. It must contain python/sdk + python/sdk-runtime with a built
# node-mode runtime carrier (see README "准备 runtime").
REPO_ROOT = Path(os.environ.get("DSH_REPO") or (WORKSPACE.parent / "deepseek-harness"))
if not (REPO_ROOT / "python" / "sdk" / "src").is_dir():
    raise SystemExit(
        f"未找到 DeepSeek Harness SDK（{REPO_ROOT}）。设置 DSH_REPO 指向 deepseek-harness 检出目录。"
    )
sys.path.insert(0, str(REPO_ROOT / "python" / "sdk" / "src"))
sys.path.insert(0, str(REPO_ROOT / "python" / "sdk-runtime" / "src"))

from deepseek_harness.client import HarnessClient, HarnessConfig  # noqa: E402
from deepseek_harness.models import Notification  # noqa: E402

STATIC_DIR = WORKSPACE / "static"
AGENT_WORKSPACE = WORKSPACE / "workspace"  # agents write their files here
SESSION_ROOT = WORKSPACE / "sessions"
BRIDGE_ROOT = WORKSPACE / ".approval-bridge"
ENV_FILE = Path(os.environ.get("DSH_ENV_FILE") or (WORKSPACE.parent / ".env"))
PORT = int(os.environ.get("WORKBENCH_PORT", "8787"))
DEFAULT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 通用 CLI 后端配置：二进制名、参数构造、安装提示。
# 环境变量 <BIN>_BIN 可覆盖二进制路径（如 CLAUDE_BIN=/path/to/claude），便于测试。
CLI_BACKENDS: dict[str, dict] = {
    "codex": {
        "bin": "codex",
        "args": lambda cwd, text: ["exec", "--json", text],
        "install": "npm install -g @openai/codex（需要 OPENAI_API_KEY）",
    },
    "claude": {
        "bin": "claude",
        "args": lambda cwd, text: ["-p", text, "--output-format", "json", "--dangerously-skip-permissions"],
        "install": "npm install -g @anthropic-ai/claude-code（需要 ANTHROPIC_API_KEY）",
    },
    "gemini": {
        "bin": "gemini",
        "args": lambda cwd, text: ["-p", text, "--output-format", "json"],
        "install": "npm install -g @google/gemini-cli（需要 GEMINI_API_KEY）",
    },
    "qwen": {
        "bin": "qwen-code",
        "args": lambda cwd, text: ["-p", text, "--output-format", "json"],
        "install": "npm install -g @qwen-code/qwen-code（需要 DASHSCOPE_API_KEY）",
    },
    "aider": {
        "bin": "aider",
        "args": lambda cwd, text: ["--message", text, "--yes-always"],
        "install": "pip install aider-chat（需要 OPENAI_API_KEY / ANTHROPIC_API_KEY）",
    },
}


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _notification_to_dict(notification: Notification) -> dict:
    return {"method": notification.method, "payload": notification.payload}


def _friendly_run_error(raw: str) -> str:
    """把 harness 原始错误翻译成用户能看懂的话。"""
    if "id collision" in raw:
        return (
            "该会话是磁盘上恢复的旧会话；harness 不允许从新进程直接续聊（持久化 id 冲突保护）。"
            "请新建一个会话继续。"
        )
    return raw


def _extract_cli_answer(stdout: str) -> str | None:
    """Best-effort parse of CLI backend stdout → the final answer text.

    Handles codex / claude / gemini style JSON: a `result` string, a `result`
    list of content blocks, or a `conversation` array with assistant messages.
    Falls back to None so callers can use the raw stdout instead.
    """
    text = stdout.strip()
    if not text:
        return None
    candidate = text
    last_line = text.splitlines()[-1].strip()
    if last_line.startswith("{"):
        candidate = last_line
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, list):
        parts = []
        for block in result:
            if isinstance(block, dict):
                t = block.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t)
        joined = "".join(parts).strip()
        if joined:
            return joined
    conv = data.get("conversation")
    if isinstance(conv, list):
        for item in reversed(conv):
            if isinstance(item, dict) and item.get("role") == "assistant":
                content = item.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    joined = "".join(parts).strip()
                    if joined:
                        return joined
    return None


def _blocks_text(content: list) -> str:
    """Join text from content blocks (user/message etc.)."""
    return "".join(str(b.get("text") or "") for b in content
                   if isinstance(b, dict) and isinstance(b.get("text"), str))


def _is_inbox_receipt(notification: Notification, session_id: str, message_id: str) -> bool:
    if notification.method != "session.event" or notification.payload.get("sessionId") != session_id:
        return False
    event = notification.payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "agent/inbox/spliced":
        return False
    data = event.get("data")
    inserted = data.get("inserted") if isinstance(data, dict) else None
    return isinstance(inserted, list) and any(
        isinstance(message, dict) and message.get("id") == message_id for message in inserted
    )


# --------------------------------------------------------------------------
# Event hub: fan-out of server events to every SSE client
# --------------------------------------------------------------------------
class EventHub:
    def __init__(self) -> None:
        self._clients: list[queue.Queue[str]] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=2000)
        with self._lock:
            self._clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def publish(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str)
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    pass


# --------------------------------------------------------------------------
# One runtime instance: a harness subprocess + its sessions + its threads
# --------------------------------------------------------------------------
class RuntimeInstance:
    def __init__(
        self,
        runtime_id: str,
        hub: EventHub,
        *,
        name: str,
        model: str,
        cwd: Path,
        session_root: Path,
        bridge_dir: Path,
        cordis_config: Path | None,
        backend: str = "harness",
    ) -> None:
        self.id = runtime_id
        self.name = name
        self.model = model
        self.cwd = cwd
        self.backend = backend
        self.hub = hub
        self.session_root = session_root
        self.bridge_dir = bridge_dir
        self.cordis_config = cordis_config
        self.client: HarnessClient | None = None
        self.sessions: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.pending_requests: dict[str, dict] = {}
        self.pending_approvals: dict[str, dict] = {}
        self.trusted_sessions: set[str] = set()
        self.notif_log: dict[str, list[dict]] = {}
        self.info: dict = {"status": "stopped", "name": name, "model": model,
                           "cwd": str(cwd), "backend": backend}
        self._request_thread: threading.Thread | None = None
        self._approval_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._log_lock = threading.Lock()
        self._seen_approvals: set[str] = set()
        self._approval_seen_at: dict[str, float] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        if self.backend == "codex":
            # Codex CLI backend: no harness subprocess; one-shot `codex exec`.
            self.info.update({"status": "running", "pid": None, "started": _now()})
            self.hub.publish({"kind": "runtime", "runtime_id": self.id, **self.info})
            return
        env = dict(os.environ)
        env["DSH_RUNTIME_MODE"] = "node"
        env["DSH_SESSION_ROOT"] = str(self.session_root)
        env["DSH_CWD"] = str(self.cwd)
        env["DSH_APPROVAL_BRIDGE"] = str(self.bridge_dir)
        if self.cordis_config is not None:
            env["DSH_CORDIS_CONFIG"] = str(self.cordis_config)
        self.client = HarnessClient(HarnessConfig(env=env, shutdown_timeout_seconds=2.0))
        self.client.start()
        self.client.initialize(cwd=str(self.cwd), provider="deepseek-official", model=self.model)
        self.info.update({
            "status": "running",
            "pid": getattr(self.client._proc, "pid", None),
            "started": _now(),
        })
        self.hub.publish({"kind": "runtime", "runtime_id": self.id, **self.info})
        self._request_thread = threading.Thread(target=self._request_loop, daemon=True)
        self._request_thread.start()
        self._approval_thread = threading.Thread(target=self._approval_watcher, daemon=True)
        self._approval_thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()
        self._recover_sessions()

    def _recover_sessions(self) -> None:
        """重启后从磁盘恢复会话列表（sessions/<runtime名>/<cwd目录>/<会话id>/session.jsonl*）。"""
        try:
            matches = sorted(self.session_root.glob("*/*/session.jsonl*"),
                             key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for log_file in matches:
            sid = log_file.parent.name
            if not sid or sid in self.sessions:
                continue
            try:
                mtime = log_file.parent.stat().st_mtime
            except OSError:
                mtime = log_file.stat().st_mtime
            self.sessions[sid] = {
                "id": sid,
                "created": time.strftime("%H:%M:%S", time.localtime(mtime)),
                "prompts": 0,
                "recovered": True,
            }
            self.hub.publish({"kind": "session-recovered", "runtime_id": self.id,
                              "session": self.sessions[sid]})

    # -- self-healing: restart the subprocess if it dies unexpectedly ---------
    def _watchdog(self) -> None:
        while True:
            time.sleep(3)
            if self.info.get("status") != "running":
                continue
            client = self.client
            if client is None:
                continue
            proc = getattr(client, "_proc", None)
            if proc is None or proc.poll() is None:
                continue  # healthy
            # Subprocess exited on its own (not via close/cancel).
            self.info["status"] = "crashed"
            self.hub.publish({"kind": "runtime", "runtime_id": self.id, "status": "crashed"})
            for sid, run in list(self.runs.items()):
                if run.get("status") == "running":
                    run["status"] = "error"
                    run["error"] = "runtime 子进程意外退出，已自动重启"
                    self.hub.publish({"kind": "run", "runtime_id": self.id,
                                      "run_id": run.get("run_id"), "session_id": sid,
                                      "status": "error", "error": run["error"]})
            threading.Thread(target=self.restart, daemon=True).start()
            return

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.close()
            finally:
                self.client = None
        self.info["status"] = "stopped"
        self.hub.publish({"kind": "runtime", "runtime_id": self.id, "status": "stopped"})

    def restart(self) -> None:
        """Tear down and relaunch the runtime subprocess (kills any stuck agent).

        Session history stays on disk (jsonl) and in this instance's in-memory
        tables, so a cancelled run's transcript survives the restart.
        """
        self.close()
        self.info = {"status": "stopped", "name": self.name, "model": self.model,
                     "cwd": str(self.cwd), "backend": self.backend}
        self._seen_approvals.clear()
        self._approval_seen_at.clear()
        self.start()

    def cancel_run(self, session_id: str) -> bool:
        run = self.runs.get(session_id)
        if run is None or run.get("status") != "running":
            return False
        run["status"] = "cancelled"
        self.hub.publish({
            "kind": "run", "runtime_id": self.id, "run_id": run.get("run_id"),
            "session_id": session_id, "status": "cancelled",
        })
        # The agent loop has no SDK cancel method; killing the subprocess is the
        # guaranteed stop. The runtime comes right back with the same identity.
        threading.Thread(target=self.restart, daemon=True).start()
        return True

    # -- runtime -> client requests, serviced generically ------------------
    def _request_loop(self) -> None:
        while True:
            client = self.client
            if client is None:
                return
            try:
                req = client.next_request()
            except BaseException as exc:
                self.hub.publish(
                    {"kind": "runtime", "runtime_id": self.id, "status": "closed", "detail": str(exc)}
                )
                return
            self.pending_requests[str(req.id)] = {
                "id": req.id, "method": req.method, "payload": req.payload,
            }
            self.hub.publish(
                {"kind": "request", "runtime_id": self.id, "id": req.id,
                 "method": req.method, "payload": req.payload}
            )

    def respond(self, request_id: str, result: object) -> bool:
        if request_id not in self.pending_requests or self.client is None:
            return False
        self.pending_requests.pop(request_id, None)
        try:
            self.client.respond(request_id, result)
        except BaseException as exc:
            self.hub.publish({"kind": "request-resolved", "id": request_id, "error": str(exc)})
            return False
        self.hub.publish({"kind": "request-resolved", "id": request_id, "result": result})
        return True

    # -- approval bridge ----------------------------------------------------
    def _approval_watcher(self) -> None:
        while True:
            try:
                files = list(self.bridge_dir.glob("pending-*.json"))
            except OSError:
                return
            now = time.monotonic()
            for path in sorted(files):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                req_id = str(data.get("id"))
                if req_id in self._seen_approvals or req_id in self.pending_approvals:
                    continue
                self._seen_approvals.add(req_id)
                self._approval_seen_at[req_id] = now
                self.pending_approvals[req_id] = data
                self.hub.publish({"kind": "approval", "runtime_id": self.id, **data})
                # Trusted session: auto-allow without waiting for the human.
                if str(data.get("agentSession")) in self.trusted_sessions:
                    self.decide_approval(req_id, "allow", auto=True)
            # The bridge plugin times out (default 120s) and settles its ask; a
            # plugin that died (runtime restart) can never clean its files, so
            # settle purely by age and remove any stale bridge files.
            for req_id in list(self.pending_approvals):
                if now - self._approval_seen_at.get(req_id, now) > 130:
                    self.pending_approvals.pop(req_id, None)
                    self.hub.publish({
                        "kind": "approval-resolved", "runtime_id": self.id,
                        "id": req_id, "decision": "unavailable",
                    })
                    for suffix in ("pending", "decision", "cancel"):
                        try:
                            (self.bridge_dir / f"{suffix}-{req_id}.json").unlink(missing_ok=True)
                        except OSError:
                            pass
            time.sleep(0.3)

    def set_trust(self, session_id: str, trust: bool) -> None:
        if trust:
            self.trusted_sessions.add(session_id)
        else:
            self.trusted_sessions.discard(session_id)
        self.hub.publish({
            "kind": "trust", "runtime_id": self.id,
            "session_id": session_id, "trusted": trust,
        })

    def decide_approval(self, request_id: str, decision: str, trust: bool = False, auto: bool = False) -> bool:
        if request_id not in self.pending_approvals or decision not in ("allow", "deny"):
            return False
        data = self.pending_approvals.pop(request_id, None)
        session_id = str(data.get("agentSession") or "")
        if trust and decision == "allow" and session_id:
            self.set_trust(session_id, True)
        target = self.bridge_dir / f"decision-{request_id}.json"
        try:
            target.write_text(json.dumps({"id": request_id, "decision": decision}), encoding="utf-8")
        except OSError as exc:
            self.hub.publish({"kind": "approval-resolved", "id": request_id, "error": str(exc)})
            return False
        self.hub.publish({
            "kind": "approval-resolved", "runtime_id": self.id,
            "id": request_id, "decision": decision, "auto": auto, "trusted": trust,
        })
        return True

    # -- sessions -----------------------------------------------------------
    def run(self, session_id: str, text: str) -> str:
        if self.info.get("status") != "running":
            raise RuntimeError("runtime is not running")
        session_id = session_id or f"session-{uuid.uuid4().hex}"
        meta = self.sessions.setdefault(session_id, {"id": session_id, "created": _now(), "prompts": 0})
        meta["prompts"] += 1
        run_id = uuid.uuid4().hex[:8]
        self.runs[session_id] = {
            "run_id": run_id, "session_id": session_id, "status": "running", "started": _now(),
        }
        self.hub.publish({
            "kind": "run", "runtime_id": self.id, "run_id": run_id,
            "session_id": session_id, "status": "started", "text": text,
        })
        threading.Thread(target=self._run_worker, args=(session_id, run_id, text), daemon=True).start()
        return run_id

    def _run_worker(self, session_id: str, run_id: str, text: str) -> None:
        if self.backend in CLI_BACKENDS:
            self._run_worker_cli(session_id, run_id, text)
            return
        client = self.client
        if client is None:
            return
        blocks = [{"type": "text", "text": text}]
        try:
            with client.subscribe_session_notifications(session_id) as sub:
                message_id = client.session_prompt(session_id, blocks, notification_subscription=sub)
                self.hub.publish({
                    "kind": "run", "runtime_id": self.id, "run_id": run_id,
                    "session_id": session_id, "status": "enqueued", "message_id": message_id,
                })
                received = False
                last_turn_error: str | None = None
                while True:
                    notification = sub.next()
                    forwarded = {
                        "kind": "notification", "runtime_id": self.id,
                        "run_id": run_id, "session_id": session_id,
                        "notification": _notification_to_dict(notification),
                    }
                    self.hub.publish(forwarded)
                    self._log_notification(session_id, {
                        "method": notification.method, "payload": notification.payload,
                        "runId": run_id, "ts": _now(),
                    })
                    if notification.method == "session.event":
                        event = notification.payload.get("event")
                        if isinstance(event, dict) and event.get("type") == "turn/end":
                            reason = (event.get("data") or {}).get("reason")
                            if isinstance(reason, dict) and reason.get("kind") == "error":
                                err = reason.get("error")
                                last_turn_error = (err.get("message") if isinstance(err, dict)
                                                   else str(err) if err is not None else "agent 回合错误")
                    if not received:
                        if not _is_inbox_receipt(notification, session_id, message_id):
                            continue
                        received = True
                    if (
                        notification.method == "session.status"
                        and notification.payload.get("sessionId") == session_id
                        and notification.payload.get("status") == "idle"
                    ):
                        break
            if last_turn_error:
                # 回合内部错误（harness 仍会发 idle，需显式标记失败）
                message = _friendly_run_error(last_turn_error)
                self.runs[session_id]["status"] = "error"
                self.runs[session_id]["error"] = message
                self.hub.publish({
                    "kind": "run", "runtime_id": self.id, "run_id": run_id,
                    "session_id": session_id, "status": "error", "error": message,
                })
                return
            self.runs[session_id]["status"] = "idle"
            self.runs[session_id]["finished"] = _now()
            self.hub.publish({
                "kind": "run", "runtime_id": self.id, "run_id": run_id,
                "session_id": session_id, "status": "idle",
            })
        except BaseException as exc:
            if self.runs.get(session_id, {}).get("status") == "cancelled":
                return  # cancelled: the subprocess was killed on purpose
            message = _friendly_run_error(str(exc))
            self.runs[session_id]["status"] = "error"
            self.runs[session_id]["error"] = message
            self.hub.publish({
                "kind": "run", "runtime_id": self.id, "run_id": run_id,
                "session_id": session_id, "status": "error", "error": message,
            })

    # -- generic CLI backends (codex / claude / gemini / qwen / aider) -------
    def _emit(self, session_id: str, run_id: str, event: dict) -> None:
        """Publish + persist one synthetic session.event (CLI backends)."""
        notification = {"method": "session.event", "payload": {"sessionId": session_id, "event": event}}
        self.hub.publish({
            "kind": "notification", "runtime_id": self.id,
            "run_id": run_id, "session_id": session_id, "notification": notification,
        })
        self._log_notification(session_id, {**notification, "runId": run_id, "ts": _now()})

    def _run_worker_cli(self, session_id: str, run_id: str, text: str) -> None:
        import shutil
        import subprocess
        spec = CLI_BACKENDS[self.backend]
        bin_name = spec["bin"]
        binary = os.environ.get(f"{bin_name.upper()}_BIN") or shutil.which(bin_name)
        self._emit(session_id, run_id, {"type": "turn/start", "data": {"turn": 1}})
        self._emit(session_id, run_id, {
            "type": "user/message",
            "data": {"content": [{"type": "text", "text": text}], "role": "user"},
        })
        label = f"[{self.backend} 后端]"
        if binary is None:
            err = f"{bin_name} CLI 未安装：{spec['install']}"
            self._emit(session_id, run_id, {"type": "assistant/message", "data": {
                "message": {"role": "assistant", "content": [{"type": "text", "text": f"{label} 不可用 {err}"}]},
            }})
            self._emit(session_id, run_id, {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "error"}}})
            self.runs[session_id]["status"] = "error"
            self.runs[session_id]["error"] = err
            self.hub.publish({"kind": "run", "runtime_id": self.id, "run_id": run_id,
                              "session_id": session_id, "status": "error", "error": err})
            return
        try:
            proc = subprocess.run(
                [binary, *spec["args"](str(self.cwd), text)],
                capture_output=True, text=True, timeout=1800, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            self._emit(session_id, run_id, {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "error"}}})
            self.runs[session_id]["status"] = "error"
            self.runs[session_id]["error"] = f"{bin_name} 执行超时（30 分钟）"
            self.hub.publish({"kind": "run", "runtime_id": self.id, "run_id": run_id,
                              "session_id": session_id, "status": "error", "error": "CLI 执行超时"})
            return
        answer = _extract_cli_answer(proc.stdout) or proc.stdout.strip() or f"({bin_name} 无输出)"
        if proc.returncode != 0 and not _extract_cli_answer(proc.stdout):
            self._emit(session_id, run_id, {"type": "assistant/message", "data": {
                "message": {"role": "assistant", "content": [{"type": "text", "text": f"{label} 错误: {answer[:500]}"}]},
            }})
            self._emit(session_id, run_id, {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "error"}}})
            self.runs[session_id]["status"] = "error"
            self.runs[session_id]["error"] = answer[:300]
            self.hub.publish({"kind": "run", "runtime_id": self.id, "run_id": run_id,
                              "session_id": session_id, "status": "error", "error": answer[:300]})
            return
        self._emit(session_id, run_id, {"type": "assistant/message", "data": {
            "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]},
        }})
        self._emit(session_id, run_id, {"type": "turn/end", "data": {"turn": 1, "reason": {"kind": "completed"}}})
        self.runs[session_id]["status"] = "idle"
        self.hub.publish({"kind": "run", "runtime_id": self.id, "run_id": run_id,
                          "session_id": session_id, "status": "idle"})

    def _log_notification(self, session_id: str, notification: dict) -> None:
        with self._log_lock:
            bucket = self.notif_log.setdefault(session_id, [])
            bucket.append(notification)
            self._trim_log(bucket)

    @staticmethod
    def _trim_log(bucket: list[dict]) -> None:
        chunks = sum(
            1 for n in bucket
            if n.get("payload", {}).get("event", {}).get("type") == "assistant/chunk"
        )
        if chunks > 300:
            to_drop = chunks - 300
            kept: list[dict] = []
            dropped = 0
            for n in bucket:
                is_chunk = n.get("payload", {}).get("event", {}).get("type") == "assistant/chunk"
                if is_chunk and dropped < to_drop:
                    dropped += 1
                    continue
                kept.append(n)
            bucket[:] = kept
        if len(bucket) > 6000:
            del bucket[: len(bucket) - 6000]

    def state(self) -> dict:
        return {
            "info": self.info,
            "sessions": sorted(self.sessions.values(), key=lambda s: s["created"]),
            "runs": self.runs,
            "pendingRequests": list(self.pending_requests.values()),
            "pendingApprovals": list(self.pending_approvals.values()),
            "trustedSessions": sorted(self.trusted_sessions),
            "notifications": self.notif_log,
        }

    def export_session(self, session_id: str) -> dict | None:
        """Export one session as JSON + a readable markdown transcript."""
        if session_id not in self.notif_log and session_id not in self.sessions:
            return None
        notifs = self.notif_log.get(session_id, [])
        lines: list[str] = [f"# 会话 {session_id}", f"runtime: {self.name}（{self.backend}）", ""]
        for n in notifs:
            if n.get("method") != "session.event":
                continue
            ev = n.get("payload", {}).get("event")
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            data = ev.get("data")
            if t == "user/message" and isinstance(data, dict):
                content = data.get("content")
                text = _blocks_text(content) if isinstance(content, list) else str(data.get("text", ""))
                if "<system-reminder>" in text or "<available_skills>" in text:
                    continue  # harness skill-catalog injection, not a real prompt
                lines.append(f"## 🧑 用户\n\n{text}\n")
            elif t == "assistant/message" and isinstance(data, dict):
                message = data.get("message") if isinstance(data.get("message"), dict) else data
                content = message.get("content")
                if isinstance(content, list):
                    parts = [b.get("text", "") for b in content
                             if isinstance(b, dict) and b.get("type") == "text"]
                    if parts:
                        lines.append(f"## 🤖 Agent\n\n{''.join(parts)}\n")
                usage = data.get("usage")
                if isinstance(usage, dict):
                    lines.append(f"*(tokens: in {usage.get('inputTokens', '?')} · out {usage.get('outputTokens', '?')} · "
                                 f"cache {usage.get('cacheReadTokens', '?')} · reasoning {usage.get('reasoningTokens', '?')})*\n")
            elif t == "tool/call" and isinstance(data, dict):
                lines.append(f"- 🛠 `{data.get('name')}` → `{data.get('arguments', '')}`\n")
        return {
            "runtime_id": self.id,
            "session_id": session_id,
            "run": self.runs.get(session_id),
            "events": notifs,
            "markdown": "\n".join(lines),
        }


# --------------------------------------------------------------------------
# Scheduled tasks (参考 OpenClaw 的 cron)：间隔 / 每日两种模式
# --------------------------------------------------------------------------
class Scheduler:
    def __init__(self, manager: "RuntimeManager", persist_path: Path) -> None:
        self.manager = manager
        self.path = persist_path
        self.hub = manager.hub
        self.tasks: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._load()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.tasks = data
        except (OSError, ValueError):
            self.tasks = {}

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def add(self, runtime_id: str, session_id: str, text: str,
            interval_minutes: float | None = None, daily: str | None = None) -> str:
        if interval_minutes is None and daily is None:
            raise ValueError("需要 interval_minutes 或 daily")
        task_id = uuid.uuid4().hex[:8]
        task = {
            "id": task_id, "runtime_id": runtime_id, "session_id": session_id, "text": text,
            "interval_minutes": interval_minutes, "daily": daily,
            "enabled": True, "last_run": None, "last_run_ts": time.time(), "last_day": None,
            "created": _now(),
        }
        with self._lock:
            self.tasks[task_id] = task
        self._save()
        self.hub.publish({"kind": "schedules", "schedules": self.state()})
        return task_id

    def remove(self, task_id: str) -> bool:
        with self._lock:
            if task_id not in self.tasks:
                return False
            del self.tasks[task_id]
        self._save()
        self.hub.publish({"kind": "schedules", "schedules": self.state()})
        return True

    def toggle(self, task_id: str) -> bool:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return False
            task["enabled"] = not task["enabled"]
        self._save()
        self.hub.publish({"kind": "schedules", "schedules": self.state()})
        return True

    def state(self) -> list[dict]:
        with self._lock:
            return list(self.tasks.values())

    def _due(self, task: dict) -> bool:
        if not task.get("enabled"):
            return False
        now = time.time()
        if task.get("interval_minutes") is not None:
            last = task.get("last_run_ts")
            if last is None:
                return False  # 首跑等一个周期，避免添加即触发
            return now - last >= float(task["interval_minutes"]) * 60
        if task.get("daily"):
            hm = task["daily"]
            try:
                h, m = map(int, str(hm).split(":"))
            except (ValueError, AttributeError):
                return False
            if task.get("last_day") == time.strftime("%Y-%m-%d"):
                return False
            now_t = time.localtime()
            return now_t.tm_hour > h or (now_t.tm_hour == h and now_t.tm_min >= m)
        return False

    def _loop(self) -> None:
        while True:
            time.sleep(15)
            with self._lock:
                due = [t for t in self.tasks.values() if self._due(t)]
            for task in due:
                inst = self.manager.get(task["runtime_id"])
                if inst is None:
                    continue
                task["last_run_ts"] = time.time()
                task["last_run"] = _now()
                if task.get("daily"):
                    task["last_day"] = time.strftime("%Y-%m-%d")
                self._save()
                self.hub.publish({"kind": "schedule-fired", "id": task["id"], "text": task["text"]})
                try:
                    inst.run(task["session_id"], task["text"])
                except RuntimeError:
                    pass


# --------------------------------------------------------------------------
# Runtime manager: N instances behind one HTTP/SSE surface
# --------------------------------------------------------------------------
class RuntimeManager:
    def __init__(self) -> None:
        self.hub = EventHub()
        self.runtimes: dict[str, RuntimeInstance] = {}
        self.scheduler = Scheduler(self, WORKSPACE / "schedules.json")

    def create(self, *, name: str | None = None, model: str | None = None,
               cwd: str | None = None, backend: str = "harness") -> RuntimeInstance:
        if backend != "harness" and backend not in CLI_BACKENDS:
            raise ValueError(f"backend 必须是 harness 或 {list(CLI_BACKENDS.keys())} 之一")
        runtime_id = uuid.uuid4().hex[:6]
        display_name = name or "default"
        # 会话目录用稳定名（slug），重启后同一 runtime 名能找回旧会话。
        stable_key = "".join(c if c.isalnum() else "-" for c in display_name).strip("-") or "default"
        AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)
        inst = RuntimeInstance(
            runtime_id,
            self.hub,
            name=display_name,
            model=model or DEFAULT_MODEL,
            cwd=Path(cwd or AGENT_WORKSPACE).resolve(),
            session_root=SESSION_ROOT / stable_key,
            bridge_dir=BRIDGE_ROOT / runtime_id,
            cordis_config=WORKSPACE / "cordis.yml" if (WORKSPACE / "cordis.yml").is_file() else None,
            backend=backend,
        )
        inst.start()
        self.runtimes[runtime_id] = inst
        return inst

    def get(self, runtime_id: str) -> RuntimeInstance | None:
        return self.runtimes.get(runtime_id)

    def remove(self, runtime_id: str) -> bool:
        inst = self.runtimes.pop(runtime_id, None)
        if inst is None:
            return False
        inst.close()
        return True

    def close_all(self) -> None:
        for inst in list(self.runtimes.values()):
            inst.close()
        self.runtimes.clear()

    def state(self) -> dict:
        return {rid: inst.state() for rid, inst in self.runtimes.items()}

# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "AgentWorkbench/1.0"
    manager: RuntimeManager | None = None

    def log_message(self, *_: object) -> None:
        pass

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        mgr = self.manager
        if mgr is None:
            self._json({"error": "not ready"}, 503)
            return
        if path == "/":
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            rel = Path(path[len("/static/"):])
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve())):
                self._json({"error": "forbidden"}, 403)
                return
            self._serve_file(target, _guess_mime(target))
        elif path == "/api/events":
            self._sse(mgr)
        elif path == "/api/state":
            self._json({"runtimes": mgr.state(), "schedules": mgr.scheduler.state()})
        else:
            parts = path.strip("/").split("/")
            if (len(parts) == 6 and parts[0] == "api" and parts[1] == "runtimes"
                    and parts[3] == "sessions" and parts[5] == "export"):
                inst = mgr.get(parts[2])
                if inst is None:
                    self._json({"error": "unknown runtime"}, 404)
                    return
                export = inst.export_session(parts[4])
                if export is None:
                    self._json({"error": "unknown session"}, 404)
                    return
                self._json(export)
            else:
                self._json({"error": f"no such route: {path}"}, 404)

    # -- POST --------------------------------------------------------------
    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        mgr = self.manager
        if mgr is None:
            self._json({"error": "not ready"}, 503)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (ValueError, json.JSONDecodeError):
            self._json({"error": "invalid JSON body"}, 400)
            return

        if path == "/api/runtimes":
            backend = str(body.get("backend") or "harness")
            try:
                inst = mgr.create(
                    name=body.get("name") if isinstance(body.get("name"), str) else None,
                    model=body.get("model") if isinstance(body.get("model"), str) else None,
                    cwd=body.get("cwd") if isinstance(body.get("cwd"), str) else None,
                    backend=backend,
                )
            except (ValueError, BaseException) as exc:
                self._json({"error": str(exc)}, 400 if isinstance(exc, ValueError) else 500)
                return
            self._json({"runtime_id": inst.id, "name": inst.name, "backend": inst.backend})
            return

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "runtimes" and parts[3] == "run":
            inst = mgr.get(parts[2])
            text = body.get("text")
            if inst is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            if not isinstance(text, str) or not text.strip():
                self._json({"error": "text is required"}, 400)
                return
            try:
                run_id = inst.run(body.get("session_id") or f"session-{uuid.uuid4().hex}", text.strip())
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 503)
                return
            self._json({"runtime_id": inst.id, "session_id": body.get("session_id"), "run_id": run_id})
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "runtimes" and parts[3] == "approval":
            inst = mgr.get(parts[2])
            request_id = str(body.get("id") or "")
            decision = str(body.get("decision") or "")
            trust = bool(body.get("trust"))
            if inst is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            if not request_id or decision not in ("allow", "deny"):
                self._json({"error": "id and decision (allow|deny) are required"}, 400)
                return
            self._json({"ok": inst.decide_approval(request_id, decision, trust=trust)})
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "runtimes" and parts[3] == "trust":
            inst = mgr.get(parts[2])
            session_id = str(body.get("session_id") or "")
            if inst is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            if not session_id:
                self._json({"error": "session_id is required"}, 400)
                return
            inst.set_trust(session_id, bool(body.get("trust")))
            self._json({"ok": True})
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "runtimes" and parts[3] == "respond":
            inst = mgr.get(parts[2])
            request_id = str(body.get("request_id") or "")
            if inst is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            self._json({"ok": inst.respond(request_id, body.get("result"))})
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "runtimes" and parts[3] == "cancel":
            inst = mgr.get(parts[2])
            session_id = str(body.get("session_id") or "")
            if inst is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            if not session_id:
                self._json({"error": "session_id is required"}, 400)
                return
            self._json({"ok": inst.cancel_run(session_id)})
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "runtimes" and parts[3] == "files":
            inst = mgr.get(parts[2])
            if inst is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            filename = str(body.get("filename") or "upload.bin")
            content = body.get("content")
            if not isinstance(content, str):
                self._json({"error": "content (base64) is required"}, 400)
                return
            try:
                raw = base64.b64decode(content)
            except (ValueError, TypeError):
                self._json({"error": "content must be valid base64"}, 400)
                return
            if len(raw) > 20 * 1024 * 1024:
                self._json({"error": "file too large (max 20MB)"}, 413)
                return
            safe_name = Path(filename).name  # strip any directory components
            target = AGENT_WORKSPACE / safe_name
            AGENT_WORKSPACE.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            session_id = str(body.get("session_id") or f"session-{uuid.uuid4().hex}")
            instruction = str(body.get("text") or "").strip()
            prompt = (instruction if instruction else "请处理我给你的这个文件")
            prompt += f"\n\n文件路径：{target}"
            try:
                run_id = inst.run(session_id, prompt)
            except RuntimeError as exc:
                self._json({"error": str(exc)}, 503)
                return
            self._json({"ok": True, "session_id": session_id, "run_id": run_id, "path": str(target)})
            return
        if path == "/api/schedules":
            runtime_id = str(body.get("runtime_id") or "")
            text = str(body.get("text") or "")
            if mgr.get(runtime_id) is None:
                self._json({"error": "unknown runtime"}, 404)
                return
            if not text.strip():
                self._json({"error": "text is required"}, 400)
                return
            interval = body.get("interval_minutes")
            daily = body.get("daily")
            if interval is not None:
                try:
                    interval = float(interval)
                except (TypeError, ValueError):
                    self._json({"error": "interval_minutes 必须是数字"}, 400)
                    return
            if daily is not None and not isinstance(daily, str):
                self._json({"error": "daily 必须是 HH:MM 字符串"}, 400)
                return
            try:
                task_id = mgr.scheduler.add(
                    runtime_id,
                    str(body.get("session_id") or f"session-{uuid.uuid4().hex}"),
                    text.strip(),
                    interval_minutes=interval,
                    daily=daily,
                )
            except ValueError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"ok": True, "id": task_id})
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "schedules" and parts[3] == "toggle":
            self._json({"ok": mgr.scheduler.toggle(parts[2])})
            return
        if path == "/api/close":
            mgr.close_all()
            self._json({"ok": True})
            return
        self._json({"error": f"no such route: {path}"}, 404)

    # -- DELETE ------------------------------------------------------------
    def do_DELETE(self) -> None:
        parts = self.path.strip("/").split("/")
        mgr = self.manager
        if mgr is None:
            self._json({"error": "not ready"}, 503)
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "runtimes":
            self._json({"ok": mgr.remove(parts[2])})
            return
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "schedules":
            self._json({"ok": mgr.scheduler.remove(parts[2])})
            return
        self._json({"error": "not found"}, 404)

    # -- helpers -----------------------------------------------------------
    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj: object, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, mgr: RuntimeManager) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sub = mgr.hub.subscribe()
        try:
            self._sse_write({"kind": "hello", "state": {"runtimes": mgr.state()}, "ts": time.time()})
            while True:
                try:
                    data = sub.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"data: " + data.encode("utf-8") + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            mgr.hub.unsubscribe(sub)

    def _sse_write(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False, default=str)
        self.wfile.write(b"data: " + data.encode("utf-8") + b"\n\n")
        self.wfile.flush()


def _guess_mime(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }.get(path.suffix.lower(), "application/octet-stream")


def main() -> None:
    env = load_dotenv(ENV_FILE)
    os.environ.update(env)
    os.environ["DSH_RUNTIME_MODE"] = "node"  # SDK launch-arg resolver reads current env

    manager = RuntimeManager()
    manager.scheduler.start()
    manager.create()  # default runtime
    Handler.manager = manager

    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Agent Desktop Workbench → http://127.0.0.1:{PORT}")
    print(f"default runtime model: {DEFAULT_MODEL}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        manager.close_all()


if __name__ == "__main__":
    main()

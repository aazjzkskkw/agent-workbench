#!/usr/bin/env python3
"""Smoke test: launch the real DeepSeek Harness runtime via the SDK and run one agent turn."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(os.environ.get("DSH_REPO") or (Path(__file__).resolve().parents[1] / "deepseek-harness"))
sys.path.insert(0, str(REPO_ROOT / "python" / "sdk" / "src"))
sys.path.insert(0, str(REPO_ROOT / "python" / "sdk-runtime" / "src"))

from deepseek_harness.client import HarnessClient, HarnessConfig
from deepseek_harness.models import Notification


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_inbox_receipt(notification: Notification, session_id: str, message_id: str) -> bool:
    if notification.method != "session.event" or notification.payload.get("sessionId") != session_id:
        return False
    event = notification.payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "agent/inbox/spliced":
        return False
    data = event.get("data")
    inserted = data.get("inserted") if isinstance(data, dict) else None
    return isinstance(inserted, list) and any(
        isinstance(m, dict) and m.get("id") == message_id for m in inserted
    )


def main() -> None:
    env = load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    os.environ.update(env)
    assert os.environ.get("DEEPSEEK_API_KEY"), "DEEPSEEK_API_KEY missing in .env"

    session_root = Path(tempfile.mkdtemp(prefix="dsh-smoke-"))
    # The SDK's launch-arg resolver reads this from the CURRENT process env.
    os.environ["DSH_RUNTIME_MODE"] = "node"
    runtime_env = dict(os.environ)
    runtime_env["DSH_SESSION_ROOT"] = str(session_root)
    runtime_env["DSH_CWD"] = str(Path(__file__).resolve().parents[1])

    client = HarnessClient(HarnessConfig(env=runtime_env, shutdown_timeout_seconds=2.0))
    client.start()
    info = client.initialize(
        cwd=str(Path(__file__).resolve().parents[1]),
        provider="deepseek-official",
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    )
    print(f"runtime: {info.serverInfo.name} {info.serverInfo.version}")

    session_id = "smoke-session"
    events: list[dict] = []
    with client.subscribe_session_notifications(session_id) as sub:
        message_id = client.session_prompt(
            session_id,
            [{"type": "text", "text": "你好，请只回复两个字：收到。"}],
            notification_subscription=sub,
        )
        print(f"message_id: {message_id}")
        received = False
        while True:
            notification = sub.next()
            if not received:
                if not is_inbox_receipt(notification, session_id, message_id):
                    continue
                received = True
            if notification.method == "session.event":
                event = notification.payload.get("event")
                if isinstance(event, dict):
                    events.append(event)
                    print(f"  event: {event.get('type')}")
            if (
                notification.method == "session.status"
                and notification.payload.get("sessionId") == session_id
                and notification.payload.get("status") == "idle"
            ):
                print("status: idle")
                break

    # final response
    final = ""
    for event in reversed(events):
        if event.get("type") != "assistant/message":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        message = data.get("message")
        content_owner = message if isinstance(message, dict) else data
        content = content_owner.get("content")
        if not isinstance(content, list):
            continue
        parts = [str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        final = "".join(parts)
        break

    print("=" * 50)
    print("final response:", final[:200])
    print("event types:", sorted({e.get("type") for e in events}))
    client.close()


if __name__ == "__main__":
    main()

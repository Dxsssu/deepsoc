#!/usr/bin/env python3
"""Terminal-based multi-agent connectivity smoke test for DeepSOC.

This script can:
1) (Optional) start web + all agent processes
2) create one test event directly in DB (without frontend)
3) poll pipeline progress and print stage status

Basic success criteria (multi-agent loop reached executor):
- Task created
- Action created
- Command created
- At least one command finished (completed/failed)

Full-cycle success hint:
- Summary generated

Usage examples:
  python tools/test_multi_agent_loop.py
  python tools/test_multi_agent_loop.py --start-agents
  python tools/test_multi_agent_loop.py --timeout 300 --poll-interval 5
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    import yaml
except Exception:
    yaml = None


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if load_dotenv:
    load_dotenv(ROOT_DIR / ".env")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DeepSOC multi-agent loop smoke test (terminal only)"
    )
    parser.add_argument(
        "--start-agents",
        action="store_true",
        help="Start main service and all role processes before testing.",
    )
    parser.add_argument(
        "--warmup-seconds",
        type=int,
        default=8,
        help="Seconds to wait after starting agents (default: 8).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Max wait time for loop completion in seconds (default: 180).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=3,
        help="Polling interval in seconds (default: 3).",
    )
    parser.add_argument(
        "--event-id",
        type=str,
        default="",
        help="Custom event_id (default: auto-generated).",
    )
    parser.add_argument(
        "--show-process-logs",
        action="store_true",
        help="Show stdout/stderr from spawned processes (default: hidden).",
    )
    return parser


def start_all_processes(show_process_logs: bool = False) -> list[subprocess.Popen]:
    commands = [
        [sys.executable, "main.py"],
        [sys.executable, "main.py", "-role", "_captain"],
        [sys.executable, "main.py", "-role", "_manager"],
        [sys.executable, "main.py", "-role", "_operator"],
        [sys.executable, "main.py", "-role", "_executor"],
        [sys.executable, "main.py", "-role", "_expert"],
    ]
    procs: list[subprocess.Popen] = []
    for cmd in commands:
        if show_process_logs:
            procs.append(subprocess.Popen(cmd, cwd=ROOT_DIR))
        else:
            procs.append(
                subprocess.Popen(
                    cmd,
                    cwd=ROOT_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
    return procs


def stop_all_processes(processes: list[subprocess.Popen]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.terminate()

    end_at = time.time() + 10
    for proc in processes:
        if proc.poll() is None:
            timeout = max(0, end_at - time.time())
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()


def create_test_event(event_id: str) -> str:
    # Lazy imports so --help works even if project deps are not installed.
    from main import app
    from app.models import Event, db

    with app.app_context():
        event = Event.query.filter_by(event_id=event_id).first()
        if event:
            return event_id

        event = Event(
            event_id=event_id,
            event_name="Terminal Multi-Agent Loop Test",
            message=(
                "SIEM告警：外部IP 11.22.33.44 对邮件网关 192.168.22.251 "
                "出现异常登录尝试，请研判并处置。"
            ),
            context="本事件由 tools/test_multi_agent_loop.py 创建，用于验证多Agent链路连通性。",
            source="tools_script",
            severity="medium",
            event_status="pending",
        )
        db.session.add(event)
        db.session.commit()
    return event_id


def fetch_snapshot(event_id: str) -> dict:
    from main import app
    from app.models import Action, Command, Event, Execution, Summary, Task

    with app.app_context():
        event = Event.query.filter_by(event_id=event_id).first()
        if not event:
            return {"exists": False}

        tasks = Task.query.filter_by(event_id=event_id).all()
        actions = Action.query.filter_by(event_id=event_id).all()
        commands = Command.query.filter_by(event_id=event_id).all()
        executions = Execution.query.filter_by(event_id=event_id).all()
        summaries = Summary.query.filter_by(event_id=event_id).all()

        command_statuses: dict[str, int] = {}
        for c in commands:
            command_statuses[c.command_status] = command_statuses.get(c.command_status, 0) + 1

        return {
            "exists": True,
            "event_status": event.event_status,
            "current_round": event.current_round,
            "task_count": len(tasks),
            "action_count": len(actions),
            "command_count": len(commands),
            "execution_count": len(executions),
            "summary_count": len(summaries),
            "command_statuses": command_statuses,
        }


def print_snapshot(elapsed: int, snap: dict) -> None:
    if not snap.get("exists"):
        print(f"[{elapsed:>4}s] event missing")
        return

    print(
        f"[{elapsed:>4}s] status={snap['event_status']}, round={snap['current_round']}, "
        f"tasks={snap['task_count']}, actions={snap['action_count']}, "
        f"commands={snap['command_count']}, executions={snap['execution_count']}, "
        f"summaries={snap['summary_count']}, command_statuses={snap['command_statuses']}"
    )


def _normalize_message_content(content: object) -> dict:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def fetch_new_llm_yaml_messages(event_id: str, last_seen_id: int) -> tuple[list[dict], int]:
    """Fetch newly created LLM-response-like messages and return payloads in dict form."""
    from main import app
    from app.models import Message

    out: list[dict] = []
    new_last_seen_id = last_seen_id

    with app.app_context():
        rows = (
            Message.query.filter(Message.event_id == event_id, Message.id > last_seen_id)
            .order_by(Message.id.asc())
            .all()
        )

        for msg in rows:
            if msg.id and msg.id > new_last_seen_id:
                new_last_seen_id = msg.id

            message_type = str(msg.message_type or "")
            if not message_type.endswith("llm_response"):
                continue

            raw_content = _normalize_message_content(msg.message_content)
            payload = raw_content.get("data", raw_content) if isinstance(raw_content, dict) else {}

            if isinstance(payload, dict) and payload:
                out.append(payload)
            else:
                out.append(
                    {
                        "type": "llm_response",
                        "from": msg.message_from,
                        "event_id": msg.event_id,
                        "round_id": msg.round_id,
                        "response_type": "UNKNOWN",
                    }
                )

    return out, new_last_seen_id


def print_yaml_payloads(payloads: list[dict]) -> None:
    if not payloads:
        return

    print(">>> New Agent LLM Responses")
    for payload in payloads:
        if yaml:
            text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()
        else:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text)
        print()


def is_basic_loop_done(snap: dict) -> bool:
    if not snap.get("exists"):
        return False

    command_statuses = snap.get("command_statuses", {})
    finished_commands = command_statuses.get("completed", 0) + command_statuses.get("failed", 0)

    return (
        snap.get("task_count", 0) > 0
        and snap.get("action_count", 0) > 0
        and snap.get("command_count", 0) > 0
        and finished_commands > 0
    )


def is_full_cycle_done(snap: dict) -> bool:
    return snap.get("summary_count", 0) > 0


def main() -> int:
    args = build_arg_parser().parse_args()

    processes: list[subprocess.Popen] = []
    shutting_down = False

    def handle_signal(*_: object) -> None:
        nonlocal shutting_down
        shutting_down = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        if args.start_agents:
            print(">>> Starting web + all agent processes ...")
            processes = start_all_processes(show_process_logs=args.show_process_logs)
            print(f">>> Waiting {args.warmup_seconds}s for warm-up ...")
            time.sleep(args.warmup_seconds)

        event_id = args.event_id.strip() or f"loop_test_{uuid.uuid4().hex[:8]}"
        create_test_event(event_id)
        print(f">>> Test event ready: {event_id}")

        started_at = time.time()
        basic_done = False
        full_done = False
        last_seen_message_id = 0

        while not shutting_down:
            elapsed = int(time.time() - started_at)
            if elapsed > args.timeout:
                break

            snap = fetch_snapshot(event_id)
            print_snapshot(elapsed, snap)
            payloads, last_seen_message_id = fetch_new_llm_yaml_messages(
                event_id, last_seen_message_id
            )
            print_yaml_payloads(payloads)

            basic_done = basic_done or is_basic_loop_done(snap)
            full_done = full_done or is_full_cycle_done(snap)

            if basic_done:
                break

            time.sleep(args.poll_interval)

        print("\n=== Result ===")
        if basic_done:
            print("PASS: basic multi-agent loop completed (reached executor stage).")
            if full_done:
                print("PASS+: summary generated (expert stage observed).")
            else:
                print("INFO: summary not observed yet (may need more time or external integrations).")
            print(f"event_id={event_id}")
            return 0

        print("FAIL: timeout before basic loop completion.")
        print(f"event_id={event_id}")
        return 1

    finally:
        if processes:
            print(">>> Shutting down spawned processes ...")
            stop_all_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())

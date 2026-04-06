#!/usr/bin/env python3
"""Terminal-based multi-agent connectivity smoke test for DeepSOC.

This script can:
1) (Optional) start web + all agent processes
2) create one test event directly in DB (without frontend)
3) poll pipeline progress and print stage status

Basic success criteria (single-round full loop):
- Round 1 has finished command(s)
- Round 1 has execution summary from expert
- Round 1 has event summary (round summary) from expert

Multi-round demo criteria (default):
- For every round in 1..target_rounds, all above conditions are met
- Default target_rounds is 2

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

PIPELINE_MESSAGE_TYPES = {
    "command_result",                # _executor
    "execution_summary_generated",   # _expert
    "event_summary_generated",       # _expert
}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DeepSOC multi-agent loop smoke test (terminal only)"
    )
    parser.add_argument(
        "--start-agents",
        action="store_true",
        default=True,
        help="Start main service and all role processes before testing (default: enabled).",
    )
    parser.add_argument(
        "--no-start-agents",
        action="store_false",
        dest="start_agents",
        help="Do not auto-start web/agent processes before testing.",
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
        default=2,
        help="Polling interval in seconds (default: 2).",
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
    parser.add_argument(
        "--target-rounds",
        type=int,
        default=2,
        help="Target rounds for multi-round demo completion (default: 2).",
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
    from app.models import Action, Command, Event, Execution, Summary, Task, TracebackTaskTree

    with app.app_context():
        event = Event.query.filter_by(event_id=event_id).first()
        if not event:
            return {"exists": False}

        tasks = Task.query.filter_by(event_id=event_id).all()
        actions = Action.query.filter_by(event_id=event_id).all()
        commands = Command.query.filter_by(event_id=event_id).all()
        executions = Execution.query.filter_by(event_id=event_id).all()
        summaries = Summary.query.filter_by(event_id=event_id).all()
        ttt_snapshots = TracebackTaskTree.query.filter_by(event_id=event_id).all()

        command_statuses: dict[str, int] = {}
        finished_command_rounds_set = set()
        for c in commands:
            command_statuses[c.command_status] = command_statuses.get(c.command_status, 0) + 1
            if c.command_status in {"completed", "failed"} and c.round_id is not None:
                finished_command_rounds_set.add(int(c.round_id))

        execution_statuses: dict[str, int] = {}
        summarized_execution_rounds_set = set()
        for ex in executions:
            execution_statuses[ex.execution_status] = execution_statuses.get(ex.execution_status, 0) + 1
            if ex.execution_status in {"summarized", "summarized_error"} and ex.round_id is not None:
                summarized_execution_rounds_set.add(int(ex.round_id))

        summary_rounds_set = set()
        for sm in summaries:
            if sm.round_id is not None:
                summary_rounds_set.add(int(sm.round_id))

        return {
            "exists": True,
            "event_status": event.event_status,
            "current_round": event.current_round,
            "task_count": len(tasks),
            "action_count": len(actions),
            "command_count": len(commands),
            "execution_count": len(executions),
            "summary_count": len(summaries),
            "ttt_version_count": len(ttt_snapshots),
            "command_statuses": command_statuses,
            "execution_statuses": execution_statuses,
            "finished_command_rounds": sorted(finished_command_rounds_set),
            "summarized_execution_rounds": sorted(summarized_execution_rounds_set),
            "summary_rounds": sorted(summary_rounds_set),
        }


def print_snapshot(elapsed: int, snap: dict) -> None:
    if not snap.get("exists"):
        print(f"[{elapsed:>4}s] event missing")
        return

    print(
        f"[{elapsed:>4}s] status={snap['event_status']}, round={snap['current_round']}, "
        f"tasks={snap['task_count']}, actions={snap['action_count']}, "
        f"commands={snap['command_count']}, executions={snap['execution_count']}, "
        f"summaries={snap['summary_count']}, ttt_versions={snap.get('ttt_version_count', 0)}, "
        f"command_statuses={snap['command_statuses']}, "
        f"execution_statuses={snap.get('execution_statuses', {})}, "
        f"finished_command_rounds={snap.get('finished_command_rounds', [])}, "
        f"summarized_execution_rounds={snap.get('summarized_execution_rounds', [])}, "
        f"summary_rounds={snap.get('summary_rounds', [])}"
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


def fetch_new_pipeline_messages(event_id: str, last_seen_id: int) -> tuple[list[dict], int]:
    """Fetch newly created pipeline messages, including executor/expert outputs."""
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
            if not (message_type.endswith("llm_response") or message_type in PIPELINE_MESSAGE_TYPES):
                continue

            raw_content = _normalize_message_content(msg.message_content)
            payload = raw_content.get("data", raw_content) if isinstance(raw_content, dict) else {}
            if not isinstance(payload, dict):
                payload = {}

            out.append(
                {
                    "id": msg.id,
                    "event_id": msg.event_id,
                    "message_type": message_type,
                    "from": msg.message_from,
                    "round_id": msg.round_id,
                    "payload": payload,
                }
            )

    return out, new_last_seen_id


def print_pipeline_messages(messages: list[dict]) -> None:
    if not messages:
        return

    print(">>> New Pipeline Messages")
    for item in messages:
        envelope = {
            "message_type": item.get("message_type"),
            "from": item.get("from"),
            "round_id": item.get("round_id"),
            "payload": item.get("payload", {}),
        }
        if yaml:
            text = yaml.safe_dump(envelope, allow_unicode=True, sort_keys=False).strip()
        else:
            text = json.dumps(envelope, ensure_ascii=False, indent=2)
        print(text)
        print()


def get_fully_completed_rounds(snap: dict) -> set[int]:
    cmd_rounds = set(snap.get("finished_command_rounds", []))
    exec_rounds = set(snap.get("summarized_execution_rounds", []))
    summary_rounds = set(snap.get("summary_rounds", []))
    return cmd_rounds & exec_rounds & summary_rounds


def is_basic_loop_done(snap: dict) -> bool:
    if not snap.get("exists"):
        return False
    return 1 in get_fully_completed_rounds(snap)


def is_multi_round_done(snap: dict, target_rounds: int) -> bool:
    if target_rounds <= 1:
        return is_basic_loop_done(snap)
    rounds = get_fully_completed_rounds(snap)
    return all(r in rounds for r in range(1, target_rounds + 1))


def main() -> int:
    args = build_arg_parser().parse_args()

    # 防止多轮演示被过短超时提前截断：
    # 按轮次设置一个保底超时预算（每轮约90秒，至少120秒）
    min_timeout = max(120, args.target_rounds * 90)
    if args.timeout < min_timeout:
        print(
            f">>> timeout too small for target_rounds={args.target_rounds}, "
            f"auto-adjust to {min_timeout}s (requested {args.timeout}s)"
        )
        args.timeout = min_timeout

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
        multi_round_done = False
        last_seen_message_id = 0

        while not shutting_down:
            elapsed = int(time.time() - started_at)
            if elapsed > args.timeout:
                break

            snap = fetch_snapshot(event_id)
            print_snapshot(elapsed, snap)
            messages, last_seen_message_id = fetch_new_pipeline_messages(
                event_id, last_seen_message_id
            )
            print_pipeline_messages(messages)

            basic_done = basic_done or is_basic_loop_done(snap)
            multi_round_done = multi_round_done or is_multi_round_done(snap, args.target_rounds)

            if multi_round_done:
                break

            time.sleep(args.poll_interval)

        print("\n=== Result ===")
        if multi_round_done:
            print(f"PASS: multi-round demo completed (target_rounds={args.target_rounds}).")
            print(
                "INFO: "
                f"basic_loop_done={basic_done}, "
                f"finished_command_rounds={snap.get('finished_command_rounds', [])}, "
                f"summarized_execution_rounds={snap.get('summarized_execution_rounds', [])}, "
                f"summary_rounds={snap.get('summary_rounds', [])}"
            )
            print(f"event_id={event_id}")
            return 0

        print(f"FAIL: timeout before reaching target_rounds={args.target_rounds}.")
        if basic_done:
            print("INFO: single-round full loop had completed, but multi-round target not reached.")
        print(
            "INFO: "
            f"finished_command_rounds={snap.get('finished_command_rounds', [])}, "
            f"summarized_execution_rounds={snap.get('summarized_execution_rounds', [])}, "
            f"summary_rounds={snap.get('summary_rounds', [])}"
        )
        print(f"event_id={event_id}")
        return 1

    finally:
        if processes:
            print(">>> Shutting down spawned processes ...")
            stop_all_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())

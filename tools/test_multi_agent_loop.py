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
  python tools/test_multi_agent_loop.py --target-rounds 3
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
        default=240,
        help="Max wait time for loop completion in seconds (default: 240).",
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
        "--target-rounds",
        type=int,
        default=2,
        help="Require loop to finish at least this many rounds (default: 2).",
    )
    parser.add_argument(
        "--skip-mcp-smoke-check",
        action="store_true",
        help="Skip active MCP tool smoke check before loop.",
    )
    parser.add_argument(
        "--skip-kb-smoke-check",
        action="store_true",
        help="Skip active knowledge-base MCP smoke check before loop.",
    )
    parser.add_argument(
        "--skip-mcp-command-check",
        action="store_true",
        help="Do not require at least one finished MCP command in pipeline.",
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
            event_name="Terminal Multi-Round Agent Loop Test",
            message=(
                "SIEM告警：检测到异常登录尝试。"
                "告警字段：@timestamp=2026-01-05 00:06:02，"
                "sip=11.22.33.44，dip=192.168.22.251。"
                "来源日志类型暂未确认（可能来自不同SIEM日志）。"
                "请先查询威胁情报与资产信息，再决定是否封禁，并持续跟进处置结果。"
            ),
            context=(
                "本事件由 tools/test_multi_agent_loop.py 创建，用于验证多Agent链路多轮循环、"
                "MCP工具执行与知识库查询能力。"
            ),
            source="tools_script",
            severity="medium",
            event_status="pending",
        )
        db.session.add(event)
        db.session.commit()
    return event_id


def run_mcp_smoke_check() -> tuple[bool, dict]:
    """Active MCP tool check to ensure mcp execution path is available."""
    from main import app
    from app.services.mcp_tool_service import MCPToolService

    with app.app_context():
        service = MCPToolService()
        result = service.execute_tool(
            server_name="threat_intel_mcp",
            tool_name="ip_reputation_lookup",
            params={"ip": "11.22.33.44", "time_window_minute": 60},
        )
    return result.get("status") == "success", result


def run_kb_smoke_check() -> tuple[bool, dict]:
    """Active KB search check through MCP server."""
    from main import app
    from app.services.mcp_tool_service import MCPToolService

    with app.app_context():
        service = MCPToolService()
        result = service.execute_tool(
            server_name="knowledge_base_mcp",
            tool_name="kb_search_for_role",
            params={
                "role": "_captain",
                "query": "SSH 异常登录 告警 研判",
                "tenant_id": "default",
                "top_k": 5,
            },
        )

    ok = result.get("status") == "success"
    if ok:
        response = (result.get("data") or {}).get("response") or {}
        hits = response.get("hits") if isinstance(response, dict) else None
        ok = bool(hits)
    return ok, result


def run_captain_kb_staged_preview(event_id: str, top_k: int = 6) -> tuple[bool, dict]:
    """Preview captain first-round staged KB retrieval (initial/cases/playbook)."""
    from main import app
    from app.models import Event
    from app.services.knowledge_base_service import get_knowledge_base_service

    with app.app_context():
        event = Event.query.filter_by(event_id=event_id).first()
        if not event:
            return False, {"status": "failed", "message": f"event not found: {event_id}"}

        kb_service = get_knowledge_base_service()
        bundle = kb_service.build_captain_first_round_kb_bundle(
            query_parts=[
                event.event_name,
                event.message,
                event.context,
                event.source,
                event.severity,
            ],
            top_k=top_k,
        )

    return True, {"status": "success", "bundle": bundle}


def print_captain_kb_staged_preview(result: dict) -> None:
    bundle = result.get("bundle") if isinstance(result, dict) else None
    if not isinstance(bundle, dict):
        return

    print(">>> Captain KB staged retrieval")
    threshold = bundle.get("cases_score_threshold")
    if threshold is not None:
        print(f"    kb_cases_threshold={float(threshold):.4f}")

    sections = [
        ("initial", "kb_security_knowledge + kb_context"),
        ("cases", "kb_cases"),
        ("playbook", "kb_playbook"),
    ]
    for key, label in sections:
        section = bundle.get(key) if isinstance(bundle, dict) else {}
        hits = section.get("hits") if isinstance(section, dict) else []
        print(f"    {label}:")
        for line in _summarize_kb_hits(hits, max_items=3):
            print(f"      {line}")
    print(f"    decision_source={bundle.get('decision_source', '')}")


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
        command_type_statuses: dict[str, dict[str, int]] = {}
        round_stats: dict[int, dict[str, int]] = {}
        finished_command_rounds: set[int] = set()
        mcp_finished_command_count = 0

        for c in commands:
            command_statuses[c.command_status] = command_statuses.get(c.command_status, 0) + 1
            ctype = str(c.command_type or "unknown")
            cstatus = str(c.command_status or "unknown")
            if ctype not in command_type_statuses:
                command_type_statuses[ctype] = {}
            command_type_statuses[ctype][cstatus] = command_type_statuses[ctype].get(cstatus, 0) + 1

            r = int(c.round_id or 0)
            round_stats.setdefault(r, {"tasks": 0, "actions": 0, "commands": 0, "finished_commands": 0})
            round_stats[r]["commands"] += 1
            if cstatus in ("completed", "failed"):
                round_stats[r]["finished_commands"] += 1
                if r > 0:
                    finished_command_rounds.add(r)
                if ctype == "mcp":
                    mcp_finished_command_count += 1

        for t in tasks:
            r = int(t.round_id or 0)
            round_stats.setdefault(r, {"tasks": 0, "actions": 0, "commands": 0, "finished_commands": 0})
            round_stats[r]["tasks"] += 1
        for a in actions:
            r = int(a.round_id or 0)
            round_stats.setdefault(r, {"tasks": 0, "actions": 0, "commands": 0, "finished_commands": 0})
            round_stats[r]["actions"] += 1

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
            "command_type_statuses": command_type_statuses,
            "mcp_finished_command_count": mcp_finished_command_count,
            "finished_command_rounds": sorted(finished_command_rounds),
            "round_stats": {str(k): v for k, v in sorted(round_stats.items(), key=lambda x: x[0])},
        }


def print_snapshot(elapsed: int, snap: dict) -> None:
    if not snap.get("exists"):
        print(f"[{elapsed:>4}s] event missing")
        return

    print(
        f"[{elapsed:>4}s] status={snap['event_status']}, round={snap['current_round']}, "
        f"tasks={snap['task_count']}, actions={snap['action_count']}, "
        f"commands={snap['command_count']}, executions={snap['execution_count']}, "
        f"summaries={snap['summary_count']}, command_statuses={snap['command_statuses']}, "
        f"mcp_finished={snap.get('mcp_finished_command_count', 0)}, "
        f"finished_rounds={snap.get('finished_command_rounds', [])}"
    )


def _preview_text(value: object, max_len: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if len(text) <= max_len else f"{text[: max_len - 3]}..."


def _safe_json_loads(value: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except Exception:
        return text


def _summarize_kb_hits(hits: object, max_items: int = 3) -> list[str]:
    if not isinstance(hits, list) or not hits:
        return ["hits=0"]

    lines = [f"hits={len(hits)}"]
    for idx, hit in enumerate(hits[:max_items], start=1):
        if not isinstance(hit, dict):
            lines.append(f"  {idx}. {_preview_text(hit, 160)}")
            continue
        title = str(hit.get("title") or hit.get("chunk_text") or "N/A").strip()
        kb_type = str(hit.get("kb_type") or "unknown")
        score = float(hit.get("score") or 0.0)
        lines.append(f"  {idx}. [{kb_type}] {title[:80]} (score={score:.4f})")
    return lines


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


def fetch_new_execution_results(event_id: str, last_seen_id: int) -> tuple[list[dict], int]:
    from main import app
    from app.models import Command, Execution

    out: list[dict] = []
    new_last_seen_id = last_seen_id

    with app.app_context():
        rows = (
            Execution.query.filter(Execution.event_id == event_id, Execution.id > last_seen_id)
            .order_by(Execution.id.asc())
            .all()
        )

        command_map: dict[str, object] = {}
        command_ids = [row.command_id for row in rows if row.command_id]
        if command_ids:
            for cmd in Command.query.filter(Command.command_id.in_(command_ids)).all():
                command_map[cmd.command_id] = cmd

        for row in rows:
            if row.id and row.id > new_last_seen_id:
                new_last_seen_id = row.id
            cmd = command_map.get(row.command_id)
            out.append(
                {
                    "id": row.id,
                    "execution_id": row.execution_id,
                    "round_id": row.round_id,
                    "execution_status": row.execution_status,
                    "execution_summary": row.execution_summary,
                    "execution_result": row.execution_result,
                    "command_id": row.command_id,
                    "command_type": getattr(cmd, "command_type", None),
                    "command_name": getattr(cmd, "command_name", None),
                    "command_entity": getattr(cmd, "command_entity", None),
                    "command_params": getattr(cmd, "command_params", None),
                }
            )

    return out, new_last_seen_id


def print_execution_results(rows: list[dict]) -> None:
    if not rows:
        return

    print(">>> New Execution Results")
    for row in rows:
        result = _safe_json_loads(row.get("execution_result"))
        if not isinstance(result, dict):
            result = {"raw": _preview_text(result, 400)}

        command_entity = row.get("command_entity")
        if not isinstance(command_entity, dict):
            command_entity = {}

        server = (
            result.get("server")
            or command_entity.get("server")
            or command_entity.get("server_name")
            or command_entity.get("mcp_server")
            or ""
        )
        tool = (
            result.get("tool")
            or command_entity.get("tool")
            or command_entity.get("tool_name")
            or command_entity.get("mcp_tool")
            or ""
        )
        response = result.get("response")

        round_id = row.get("round_id")
        status = row.get("execution_status")
        cmd_name = _preview_text(row.get("command_name"), 120) or "N/A"
        cmd_type = row.get("command_type") or "unknown"

        if server == "knowledge_base_mcp":
            print(
                f"- [KB] round={round_id}, status={status}, tool={tool}, "
                f"command_type={cmd_type}, command={cmd_name}"
            )
            kb_hits = []
            if isinstance(response, dict):
                kb_hits = response.get("hits") if isinstance(response.get("hits"), list) else []
            for line in _summarize_kb_hits(kb_hits):
                print(f"    {line}")
            if isinstance(response, dict):
                kb_context = _preview_text(response.get("kb_context"), 220)
                if kb_context:
                    print(f"    kb_context={kb_context}")
            continue

        if server:
            print(
                f"- [MCP] round={round_id}, status={status}, server={server}, tool={tool}, "
                f"command_type={cmd_type}, command={cmd_name}"
            )
            if isinstance(response, dict):
                preview = _preview_text(json.dumps(response, ensure_ascii=False), 280)
                if preview:
                    print(f"    response={preview}")
            else:
                raw_preview = _preview_text(response or result, 280)
                if raw_preview:
                    print(f"    response={raw_preview}")
            continue

        print(
            f"- [EXEC] round={round_id}, status={status}, command_type={cmd_type}, "
            f"command={cmd_name}, summary={_preview_text(row.get('execution_summary'), 220)}"
        )


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


def fetch_new_expert_messages(event_id: str, last_seen_id: int) -> tuple[list[dict], int]:
    """Fetch newly created messages from _expert."""
    from main import app
    from app.models import Message

    out: list[dict] = []
    new_last_seen_id = last_seen_id

    with app.app_context():
        rows = (
            Message.query.filter(
                Message.event_id == event_id,
                Message.id > last_seen_id,
                Message.message_from == "_expert",
            )
            .order_by(Message.id.asc())
            .all()
        )

        for msg in rows:
            if msg.id and msg.id > new_last_seen_id:
                new_last_seen_id = msg.id

            raw_content = _normalize_message_content(msg.message_content)
            payload = raw_content.get("data", raw_content) if isinstance(raw_content, dict) else raw_content
            out.append(
                {
                    "id": msg.id,
                    "message_type": str(msg.message_type or ""),
                    "from": str(msg.message_from or ""),
                    "round_id": msg.round_id,
                    "payload": payload,
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


def print_expert_messages(messages: list[dict]) -> None:
    if not messages:
        return

    print(">>> New Expert Messages")
    for item in messages:
        print(
            f"- type={item.get('message_type', '')}, "
            f"from={item.get('from', '')}, round={item.get('round_id', '')}"
        )
        payload = item.get("payload")
        if isinstance(payload, dict):
            if yaml:
                text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False).strip()
            else:
                text = json.dumps(payload, ensure_ascii=False, indent=2)
            print(_preview_text(text, 1400))
        else:
            print(_preview_text(payload, 800))
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


def is_target_rounds_done(snap: dict, target_rounds: int) -> bool:
    if target_rounds <= 1:
        return is_basic_loop_done(snap)
    finished_rounds = set(snap.get("finished_command_rounds", []))
    required_rounds = set(range(1, target_rounds + 1))
    return required_rounds.issubset(finished_rounds)


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

        captain_kb_preview_ok, captain_kb_preview = run_captain_kb_staged_preview(event_id)
        print(f">>> Captain KB staged preview: {'PASS' if captain_kb_preview_ok else 'FAIL'}")
        if captain_kb_preview_ok:
            print_captain_kb_staged_preview(captain_kb_preview)
        else:
            print(json.dumps(captain_kb_preview, ensure_ascii=False, indent=2))

        mcp_smoke_ok = True
        kb_smoke_ok = True
        if not args.skip_mcp_smoke_check:
            mcp_smoke_ok, mcp_smoke_result = run_mcp_smoke_check()
            print(f">>> MCP smoke check: {'PASS' if mcp_smoke_ok else 'FAIL'}")
            if mcp_smoke_ok:
                mcp_data = (mcp_smoke_result.get("data") or {}) if isinstance(mcp_smoke_result, dict) else {}
                server = mcp_data.get("server")
                tool = mcp_data.get("tool")
                response = mcp_data.get("response")
                print(
                    ">>> MCP smoke result: "
                    f"server={server}, tool={tool}, response={_preview_text(response, 280)}"
                )
            else:
                print(json.dumps(mcp_smoke_result, ensure_ascii=False, indent=2))
        if not args.skip_kb_smoke_check:
            kb_smoke_ok, kb_smoke_result = run_kb_smoke_check()
            print(f">>> KB smoke check: {'PASS' if kb_smoke_ok else 'FAIL'}")
            if kb_smoke_ok:
                kb_response = ((kb_smoke_result.get("data") or {}).get("response") or {})
                print(">>> KB smoke result:")
                for line in _summarize_kb_hits(kb_response.get("hits")):
                    print(f"    {line}")
            else:
                print(json.dumps(kb_smoke_result, ensure_ascii=False, indent=2))

        started_at = time.time()
        basic_done = False
        full_done = False
        rounds_done = False
        mcp_command_done = False
        last_seen_message_id = 0
        last_seen_expert_message_id = 0
        last_seen_execution_id = 0

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
            expert_messages, last_seen_expert_message_id = fetch_new_expert_messages(
                event_id, last_seen_expert_message_id
            )
            print_expert_messages(expert_messages)
            execution_rows, last_seen_execution_id = fetch_new_execution_results(
                event_id, last_seen_execution_id
            )
            print_execution_results(execution_rows)

            basic_done = basic_done or is_basic_loop_done(snap)
            full_done = full_done or is_full_cycle_done(snap)
            rounds_done = rounds_done or is_target_rounds_done(snap, max(1, args.target_rounds))
            mcp_command_done = mcp_command_done or (snap.get("mcp_finished_command_count", 0) > 0)

            mcp_pipeline_ok = args.skip_mcp_command_check or mcp_command_done
            if basic_done and rounds_done and mcp_pipeline_ok:
                break

            time.sleep(args.poll_interval)

        print("\n=== Result ===")
        required_smoke_ok = mcp_smoke_ok and kb_smoke_ok
        required_pipeline_ok = basic_done and rounds_done and (
            args.skip_mcp_command_check or mcp_command_done
        )
        if required_smoke_ok and required_pipeline_ok:
            print("PASS: multi-round multi-agent loop completed.")
            print(f"PASS: reached target_rounds={max(1, args.target_rounds)}.")
            if not args.skip_mcp_command_check:
                print("PASS: at least one MCP command finished inside pipeline.")
            if not args.skip_mcp_smoke_check:
                print("PASS: MCP smoke check succeeded.")
            if not args.skip_kb_smoke_check:
                print("PASS: KB MCP search smoke check succeeded.")
            if full_done:
                print("PASS+: summary generated (expert stage observed).")
            else:
                print("INFO: summary not observed yet (may need more time or external integrations).")
            print(f"round_stats={snap.get('round_stats', {})}")
            print(f"event_id={event_id}")
            return 0

        if not required_pipeline_ok:
            print("FAIL: timeout before required multi-round pipeline completion.")
            print(f"INFO: basic_done={basic_done}, rounds_done={rounds_done}, mcp_command_done={mcp_command_done}")
        if not required_smoke_ok:
            print("FAIL: pre-loop smoke checks did not pass.")
            print(f"INFO: mcp_smoke_ok={mcp_smoke_ok}, kb_smoke_ok={kb_smoke_ok}")
        print(f"round_stats={snap.get('round_stats', {}) if 'snap' in locals() else {}}")
        print(f"event_id={event_id}")
        return 1

    finally:
        if processes:
            print(">>> Shutting down spawned processes ...")
            stop_all_processes(processes)


if __name__ == "__main__":
    raise SystemExit(main())

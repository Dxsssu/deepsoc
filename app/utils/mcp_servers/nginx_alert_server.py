from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from fastmcp import FastMCP

from app.utils.mcp_servers._alert_query_utils import (
    contains_ip,
    guess_message,
    load_json_array,
    parse_time_input,
    parse_timestamp,
    select_first_ip,
)

mcp = FastMCP("nginx_alert_mcp")

ROOT_DIR = Path(__file__).resolve().parents[3]
ALERT_DIR = ROOT_DIR / "data" / "alerts" / "tests_5000"
ALERT_FILE = "cty-nginx.json"
TIME_FIELD = "@timestamp"
SIP_FIELDS = ["remote_addr", "http_x_forwarded_for"]
DIP_FIELDS = ["dst_ip", "dip", "log_from"]

_CACHE_LOADED = False
_CACHE_ROWS: List[Dict[str, object]] = []


def _load_cache() -> None:
    global _CACHE_LOADED, _CACHE_ROWS
    if _CACHE_LOADED:
        return

    path = ALERT_DIR / ALERT_FILE
    rows: List[Dict[str, object]] = []
    for idx, item in enumerate(load_json_array(path)):
        ts_raw = item.get(TIME_FIELD)
        rows.append(
            {
                "_source_file": ALERT_FILE,
                "_index": idx,
                "_timestamp_raw": ts_raw,
                "_timestamp_dt": parse_timestamp(ts_raw),
                "_sip": select_first_ip(item, SIP_FIELDS),
                "_dip": select_first_ip(item, DIP_FIELDS),
                "_record": item,
            }
        )
    _CACHE_ROWS = rows
    _CACHE_LOADED = True


@mcp.tool(name="nginx_list_meta", description="查看 Nginx 告警源信息与字段映射")
def nginx_list_meta() -> Dict[str, object]:
    _load_cache()
    return {
        "status": "success",
        "data": {
            "alert_type": "cty_nginx",
            "alert_file": ALERT_FILE,
            "alert_dir": str(ALERT_DIR),
            "count": len(_CACHE_ROWS),
            "time_field": TIME_FIELD,
            "sip_fields": SIP_FIELDS,
            "dip_fields": DIP_FIELDS,
        },
    }


@mcp.tool(
    name="nginx_query_alerts",
    description="按时间范围、sip、dip 组合查询 Nginx 告警（AND 条件）",
)
def nginx_query_alerts(
    start_time: str = "",
    end_time: str = "",
    sip: str = "",
    dip: str = "",
    limit: int = 50,
    include_raw: bool = False,
) -> Dict[str, object]:
    try:
        _load_cache()
        start_dt = parse_time_input(start_time, "start_time")
        end_dt = parse_time_input(end_time, "end_time")
        if start_dt and end_dt and start_dt > end_dt:
            return {"status": "failed", "message": "start_time must be <= end_time"}
    except Exception as exc:
        return {"status": "failed", "message": str(exc)}

    safe_limit = max(1, min(int(limit), 500))
    sip_query = (sip or "").strip()
    dip_query = (dip or "").strip()

    matched: List[Dict[str, object]] = []
    for row in _CACHE_ROWS:
        ts_dt = row.get("_timestamp_dt")
        if start_dt is not None:
            if ts_dt is None or ts_dt < start_dt:
                continue
        if end_dt is not None:
            if ts_dt is None or ts_dt > end_dt:
                continue

        record = row.get("_record")
        if not isinstance(record, dict):
            continue
        if sip_query and not contains_ip(record, SIP_FIELDS, sip_query):
            continue
        if dip_query and not contains_ip(record, DIP_FIELDS, dip_query):
            continue

        item: Dict[str, object] = {
            "alert_type": "cty_nginx",
            "source_file": row.get("_source_file"),
            "index": row.get("_index"),
            "timestamp": row.get("_timestamp_raw"),
            "sip": row.get("_sip"),
            "dip": row.get("_dip"),
            "message": guess_message(
                record,
                ["content", "request", "description", "attack_type", "event_type", "uri"],
            ),
        }
        if include_raw:
            item["record"] = record
        matched.append(item)
        if len(matched) >= safe_limit:
            break

    return {
        "status": "success",
        "data": {
            "query": {
                "start_time": start_time or "",
                "end_time": end_time or "",
                "sip": sip_query,
                "dip": dip_query,
                "limit": safe_limit,
                "include_raw": bool(include_raw),
            },
            "count": len(matched),
            "items": matched,
        },
    }


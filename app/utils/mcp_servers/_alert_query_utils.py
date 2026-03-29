from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def parse_timestamp(ts: object) -> Optional[datetime]:
    if ts is None:
        return None
    text = str(ts).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def parse_time_input(value: str, field_name: str) -> Optional[datetime]:
    text = (value or "").strip()
    if not text:
        return None
    dt = parse_timestamp(text)
    if dt is None:
        raise ValueError(
            f"invalid {field_name}: {value}. expected format like 2026-01-05 12:00:00"
        )
    return dt


def extract_ips(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            out.extend(extract_ips(item))
        return out
    if isinstance(value, dict):
        out: List[str] = []
        for item in value.values():
            out.extend(extract_ips(item))
        return out

    text = str(value).strip()
    if not text:
        return []
    return re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)


def select_first_ip(record: Dict[str, object], fields: List[str]) -> Optional[str]:
    for field in fields:
        if field not in record:
            continue
        ips = extract_ips(record.get(field))
        if ips:
            return ips[0]
    return None


def contains_ip(record: Dict[str, object], fields: List[str], ip: str) -> bool:
    target = (ip or "").strip()
    if not target:
        return True
    for field in fields:
        if field not in record:
            continue
        ips = extract_ips(record.get(field))
        if target in ips:
            return True
    return False


def guess_message(record: Dict[str, object], candidate_fields: List[str]) -> str:
    for field in candidate_fields:
        value = record.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            text = ",".join([str(x) for x in value if str(x).strip()])
        else:
            text = str(value).strip()
        if text:
            return text
    return ""


def load_json_array(path: Path) -> List[Dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    out: List[Dict[str, object]] = []
    for item in payload:
        if isinstance(item, dict):
            out.append(item)
    return out


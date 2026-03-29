import hashlib
import ipaddress
from typing import Dict, List

from fastmcp import FastMCP

mcp = FastMCP("threat_intel_mcp")


def _score_to_level(score: int) -> str:
    if score >= 80:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _base_tags(score: int, is_private: bool) -> List[str]:
    if is_private:
        return ["internal_address", "not_public_intel"]
    if score >= 80:
        return ["scanner", "brute_force", "known_malicious"]
    if score >= 50:
        return ["suspicious_activity"]
    return ["clean_history"]


@mcp.tool(name="ip_reputation_lookup", description="查询IP信誉评分、风险等级和标签")
def ip_reputation_lookup(ip: str, time_window_minute: int = 60) -> Dict[str, object]:
    """Simple built-in IP reputation tool for SOC workflow testing."""
    parsed_ip = ipaddress.ip_address(str(ip).strip())
    is_private = parsed_ip.is_private

    if is_private:
        score = 5
    else:
        digest = hashlib.sha256(str(parsed_ip).encode("utf-8")).hexdigest()
        score = int(digest[:2], 16) % 101

    level = _score_to_level(score)
    tags = _base_tags(score, is_private)

    return {
        "ip": str(parsed_ip),
        "time_window_minute": int(time_window_minute),
        "reputation_score": score,
        "risk_level": level,
        "tags": tags,
        "source": "built_in_threat_intel_mcp",
    }

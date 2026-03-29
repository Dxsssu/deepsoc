import hashlib
import ipaddress
from typing import Dict, List

from fastmcp import FastMCP

mcp = FastMCP("ip_info_mcp")


def _pseudo_location(ip: str) -> Dict[str, str]:
    digest = hashlib.md5(ip.encode("utf-8")).hexdigest()
    candidates: List[Dict[str, str]] = [
        {"country": "US", "region": "California", "city": "San Jose"},
        {"country": "CN", "region": "Guangdong", "city": "Shenzhen"},
        {"country": "SG", "region": "Singapore", "city": "Singapore"},
        {"country": "DE", "region": "Hesse", "city": "Frankfurt"},
        {"country": "JP", "region": "Tokyo", "city": "Tokyo"},
    ]
    idx = int(digest[:2], 16) % len(candidates)
    return candidates[idx]


@mcp.tool(name="ip_info_lookup", description="查询IP基础信息（归属、网络类型、ASN等）")
def ip_info_lookup(ip: str) -> Dict[str, object]:
    parsed_ip = ipaddress.ip_address(str(ip).strip())
    is_private = parsed_ip.is_private
    is_loopback = parsed_ip.is_loopback
    is_multicast = parsed_ip.is_multicast

    if is_private or is_loopback:
        location = {"country": "Internal", "region": "LAN", "city": "PrivateNetwork"}
        isp = "Internal Network"
        asn = "AS00000"
        network_type = "private"
    else:
        location = _pseudo_location(str(parsed_ip))
        asn_seed = int(hashlib.sha1(str(parsed_ip).encode("utf-8")).hexdigest()[:6], 16)
        asn = f"AS{asn_seed % 64512 + 1024}"
        isp = "Example Transit ISP"
        network_type = "public"

    return {
        "ip": str(parsed_ip),
        "version": parsed_ip.version,
        "network_type": network_type,
        "is_private": is_private,
        "is_loopback": is_loopback,
        "is_multicast": is_multicast,
        "asn": asn,
        "isp": isp,
        "geo": location,
        "source": "built_in_ip_info_mcp",
    }


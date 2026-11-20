"""Endpoint URL validation and secret masking."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

SENSITIVE_HEADER_HINTS = ("auth", "key", "token", "secret", "password", "cookie", "session")


@dataclass
class UrlCheck:
    ok: bool
    reason: str = ""
    resolved_ips: tuple[str, ...] = ()


def validate_endpoint_url(url: str, security: dict[str, Any], resolve: bool = True) -> UrlCheck:
    """Check that ``url`` points at an approved local host.

    Rules: http/https only, no embedded credentials, port not on the blocked
    list, and the host is either explicitly allowlisted or *every* address it
    resolves to falls inside an allowed network.
    """
    if not url or not isinstance(url, str):
        return UrlCheck(False, "URL is empty.")
    url = url.strip()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        return UrlCheck(False, f"Malformed URL: {exc}")

    if parts.scheme not in ("http", "https"):
        return UrlCheck(False, "Only http:// and https:// URLs are allowed.")
    if not parts.hostname:
        return UrlCheck(False, "URL has no host.")
    if parts.username or parts.password:
        return UrlCheck(False, "Credentials embedded in the URL are not allowed.")
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    blocked_ports = {int(p) for p in security.get("blocked_ports", [])}
    if port in blocked_ports:
        return UrlCheck(False, f"Port {port} is on the blocked list (sensitive local service).")

    host = parts.hostname.lower()
    allowed_hosts = {h.lower() for h in security.get("allowed_hosts", [])}
    networks = []
    for net in security.get("allowed_networks", []):
        try:
            networks.append(ipaddress.ip_network(net, strict=False))
        except ValueError:
            continue

    if host in allowed_hosts:
        return UrlCheck(True, "Host is explicitly allowlisted.")

    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        if not resolve:
            return UrlCheck(False, f"Host '{host}' is not an IP address and is not allowlisted.")
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            return UrlCheck(False, f"Could not resolve host '{host}': {exc}")
        addresses = sorted({ipaddress.ip_address(info[4][0]) for info in infos}, key=str)

    if not addresses:
        return UrlCheck(False, f"Host '{host}' did not resolve to any address.")
    for addr in addresses:
        if not any(addr.version == net.version and addr in net for net in networks):
            return UrlCheck(
                False,
                f"Address {addr} is outside the allowed networks. "
                "Add it to security.allowed_networks or the host to security.allowed_hosts.",
            )
    return UrlCheck(True, "Address is inside an allowed network.", tuple(str(a) for a in addresses))


def is_sensitive_header(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in SENSITIVE_HEADER_HINTS)


def mask_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    """Return headers with every value masked, so they are safe to display."""
    if not headers:
        return {}
    return {name: "****" for name in headers}


def mask_request_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = dict(config)
    if "headers" in masked:
        masked["headers"] = mask_headers(masked.get("headers"))
    return masked

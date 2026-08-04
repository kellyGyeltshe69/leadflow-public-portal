from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "host.docker.internal",
    "ollama",
    "app",
}


def hostname_looks_public(hostname: str | None, *, resolve_dns: bool = True) -> bool:
    if not hostname:
        return False
    host = hostname.rstrip(".").lower()
    if host in BLOCKED_HOSTS or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        return literal.is_global
    except ValueError:
        pass
    # Single-label hostnames are normally internal Docker/LAN/service names.
    if "." not in host:
        return False
    if not resolve_dns:
        return True
    try:
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    addresses = {str(record[4][0]).split("%", 1)[0] for record in records}
    if not addresses:
        return False
    try:
        return all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def public_http_url(url: str, *, resolve_dns: bool = True) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.port and parsed.port not in {80, 443}:
        # Website research has no need to access arbitrary administrative ports.
        return False
    return hostname_looks_public(parsed.hostname, resolve_dns=resolve_dns)

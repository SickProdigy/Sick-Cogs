"""Validated DNS and SRV resolution for Minecraft status checks."""

import asyncio
import ipaddress
import re
import socket
from typing import Iterable, Optional, Tuple

import dns.asyncresolver
import dns.exception
import dns.resolver


_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


class MinecraftQueryError(RuntimeError):
    pass


def normalize_host(value: str) -> str:
    host = str(value or "").strip().rstrip(".")
    if not host or "://" in host or not _HOST_RE.fullmatch(host):
        raise ValueError("Enter a hostname or IP address without a URL or path.")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            return host.encode("idna").decode("ascii").casefold()
        except UnicodeError as exc:
            raise ValueError("Enter a valid hostname or IP address.") from exc


def address_allowed(address: str, *, private_allowed: bool = False) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return False
    return not ip.is_private or private_allowed


async def java_target(host: str, port: Optional[int], *, timeout: float = 3.0):
    host = normalize_host(host)
    if port is not None:
        if not 1 <= int(port) <= 65535:
            raise ValueError("The port must be from 1 to 65535.")
        return host, int(port), host
    try:
        answers = await asyncio.wait_for(
            dns.asyncresolver.resolve(f"_minecraft._tcp.{host}", "SRV"),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, dns.exception.DNSException):
        return host, 25565, host
    records = sorted(
        answers,
        key=lambda answer: (int(answer.priority), -int(answer.weight), str(answer.target)),
    )
    if not records:
        return host, 25565, host
    selected = records[0]
    return normalize_host(str(selected.target)), int(selected.port), host


async def resolve_socket_target(
    target_host: str,
    port: int,
    *,
    policy_host: str,
    private_hosts: Iterable[str] = (),
    socktype: int,
    timeout: float = 3.0,
):
    allowed = {normalize_host(item).casefold() for item in private_hosts}
    allow_private = normalize_host(policy_host).casefold() in allowed
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                target_host,
                int(port),
                socket.AF_UNSPEC,
                socktype,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise MinecraftQueryError("DNS resolution timed out.") from exc
    except OSError as exc:
        raise MinecraftQueryError("The server hostname could not be resolved.") from exc
    candidates = []
    for family, resolved_type, protocol, _canonname, sockaddr in records:
        if resolved_type != socktype:
            continue
        if address_allowed(sockaddr[0], private_allowed=allow_private):
            candidates.append((family, protocol, sockaddr))
    if not candidates:
        raise MinecraftQueryError(
            "The hostname did not resolve to an allowed address. Private/LAN hosts require bot-owner approval."
        )
    candidates.sort(key=lambda item: ipaddress.ip_address(item[2][0].split("%", 1)[0]).is_private)
    return candidates[0]

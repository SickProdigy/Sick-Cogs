"""Bounded Source A2S_INFO querying for Garry's Mod servers."""

import asyncio
import ipaddress
import re
import socket
import struct
import time
from typing import Iterable, Optional


INFO_REQUEST = b"\xff\xff\xff\xffTSource Engine Query\x00"
MAX_PACKET_BYTES = 65535
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")


class A2SError(RuntimeError):
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


async def resolve_target(
    host: str,
    port: int,
    *,
    private_hosts: Iterable[str] = (),
    timeout: float = 3.0,
):
    host = normalize_host(host)
    if not 1 <= int(port) <= 65535:
        raise ValueError("The query port must be from 1 to 65535.")
    allow_private = host.casefold() in {normalize_host(item).casefold() for item in private_hosts}
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                int(port),
                socket.AF_UNSPEC,
                socket.SOCK_DGRAM,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        raise A2SError("DNS resolution timed out.") from exc
    except OSError as exc:
        raise A2SError("The server hostname could not be resolved.") from exc

    candidates = []
    for family, socktype, protocol, _canonname, sockaddr in records:
        if socktype != socket.SOCK_DGRAM:
            continue
        if address_allowed(sockaddr[0], private_allowed=allow_private):
            candidates.append((family, protocol, sockaddr))
    if not candidates:
        raise A2SError(
            "The hostname did not resolve to an allowed address. Private/LAN hosts require bot-owner approval."
        )
    candidates.sort(key=lambda item: ipaddress.ip_address(item[2][0].split("%", 1)[0]).is_private)
    return host, candidates[0]


class _Reader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.payload):
            raise A2SError("The server returned a truncated A2S_INFO response.")
        value = self.payload[self.offset:end]
        self.offset = end
        return value

    def number(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def cstring(self, *, limit: int = 2048) -> str:
        end = self.payload.find(b"\x00", self.offset)
        if end < 0 or end - self.offset > limit:
            raise A2SError("The server returned an invalid A2S_INFO string.")
        value = self.payload[self.offset:end]
        self.offset = end + 1
        return value.decode("utf-8", "replace")


def parse_info_packet(payload: bytes) -> dict:
    if payload.startswith(b"\xfe\xff\xff\xff"):
        raise A2SError("Split A2S_INFO responses are not supported by this bounded status check.")
    if not payload.startswith(b"\xff\xff\xff\xffI"):
        raise A2SError("The server returned an unexpected A2S_INFO response.")
    reader = _Reader(payload[5:])
    result = {
        "protocol": reader.number("<B"),
        "name": reader.cstring(),
        "map": reader.cstring(),
        "folder": reader.cstring(),
        "game": reader.cstring(),
        "app_id": reader.number("<H"),
        "players": reader.number("<B"),
        "max_players": reader.number("<B"),
        "bots": reader.number("<B"),
        "server_type": chr(reader.number("<B")),
        "environment": chr(reader.number("<B")),
        "password": bool(reader.number("<B")),
        "vac": bool(reader.number("<B")),
        "version": reader.cstring(),
    }
    if reader.offset < len(reader.payload):
        edf = reader.number("<B")
        if edf & 0x80:
            result["port"] = reader.number("<H")
        if edf & 0x10:
            result["steam_id"] = str(reader.number("<Q"))
        if edf & 0x40:
            result["spectator_port"] = reader.number("<H")
            result["spectator_name"] = reader.cstring()
        if edf & 0x20:
            result["keywords"] = reader.cstring()
        if edf & 0x01:
            result["game_id"] = str(reader.number("<Q"))
    return result


def _query_address(family: int, protocol: int, sockaddr, timeout: float):
    sock = socket.socket(family, socket.SOCK_DGRAM, protocol)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        started = time.monotonic()
        sock.send(INFO_REQUEST)
        payload = sock.recv(MAX_PACKET_BYTES + 1)
        if len(payload) > MAX_PACKET_BYTES:
            raise A2SError("The server response exceeded the packet-size limit.")
        if payload.startswith(b"\xff\xff\xff\xffA"):
            if len(payload) != 9:
                raise A2SError("The server returned an invalid A2S challenge.")
            sock.send(INFO_REQUEST + payload[5:9])
            payload = sock.recv(MAX_PACKET_BYTES + 1)
        latency_ms = round((time.monotonic() - started) * 1000)
    except socket.timeout as exc:
        raise A2SError("The server query timed out.") from exc
    except OSError as exc:
        raise A2SError("The server could not be reached.") from exc
    finally:
        sock.close()
    if len(payload) > MAX_PACKET_BYTES:
        raise A2SError("The server response exceeded the packet-size limit.")
    return payload, latency_ms


class A2SClient:
    def __init__(self, *, timeout: float = 3.0):
        self.timeout = max(0.5, min(float(timeout), 10.0))

    async def info(self, host: str, port: int, *, private_hosts: Iterable[str] = ()) -> dict:
        public_host, target = await resolve_target(
            host,
            port,
            private_hosts=private_hosts,
            timeout=self.timeout,
        )
        family, protocol, sockaddr = target
        try:
            payload, latency_ms = await asyncio.wait_for(
                asyncio.to_thread(
                    _query_address,
                    family,
                    protocol,
                    sockaddr,
                    self.timeout,
                ),
                timeout=self.timeout + 0.5,
            )
        except asyncio.TimeoutError as exc:
            raise A2SError("The server query timed out.") from exc
        result = parse_info_packet(payload)
        result["host"] = public_host
        result["query_port"] = int(port)
        result["latency_ms"] = latency_ms
        return result

"""Bounded Minecraft Java SLP and Bedrock RakNet status clients."""

import asyncio
import json
import os
import socket
import struct
import time
from typing import Iterable, Optional

from .network import MinecraftQueryError, java_target, normalize_host, resolve_socket_target


MAX_JSON_BYTES = 65535
MAX_BEDROCK_BYTES = 4096
BEDROCK_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


def encode_varint(value: int) -> bytes:
    value &= 0xFFFFFFFF
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        output.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(output)


async def read_varint(reader: asyncio.StreamReader) -> int:
    value = 0
    for position in range(5):
        byte = (await reader.readexactly(1))[0]
        value |= (byte & 0x7F) << (7 * position)
        if not byte & 0x80:
            return value
    raise MinecraftQueryError("The Java server returned an oversized VarInt.")


def encode_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return encode_varint(len(data)) + data


def flatten_description(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(flatten_description(item) for item in value)
    if not isinstance(value, dict):
        return str(value or "")
    return str(value.get("text") or "") + flatten_description(value.get("extra") or [])


def parse_java_status(payload: bytes) -> dict:
    if len(payload) > MAX_JSON_BYTES:
        raise MinecraftQueryError("The Java status response exceeded the size limit.")
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinecraftQueryError("The Java server returned invalid status JSON.") from exc
    if not isinstance(data, dict):
        raise MinecraftQueryError("The Java server returned an invalid status object.")
    version = data.get("version") if isinstance(data.get("version"), dict) else {}
    players = data.get("players") if isinstance(data.get("players"), dict) else {}
    sample = players.get("sample") if isinstance(players.get("sample"), list) else []
    return {
        "motd": flatten_description(data.get("description")),
        "version": str(version.get("name") or "Unknown"),
        "protocol": int(version.get("protocol") or 0),
        "players": int(players.get("online") or 0),
        "max_players": int(players.get("max") or 0),
        "sample": [
            str(item.get("name"))
            for item in sample[:20]
            if isinstance(item, dict) and item.get("name")
        ],
    }


async def query_java(
    host: str,
    port: Optional[int],
    *,
    private_hosts: Iterable[str] = (),
    timeout: float = 3.0,
) -> dict:
    target_host, target_port, policy_host = await java_target(host, port, timeout=timeout)
    family, protocol, sockaddr = await resolve_socket_target(
        target_host,
        target_port,
        policy_host=policy_host,
        private_hosts=private_hosts,
        socktype=socket.SOCK_STREAM,
        timeout=timeout,
    )
    started = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(sockaddr[0], sockaddr[1], family=family),
            timeout=timeout,
        )
        handshake = (
            encode_varint(0)
            + encode_varint(-1)
            + encode_string(normalize_host(host))
            + struct.pack(">H", target_port)
            + encode_varint(1)
        )
        writer.write(encode_varint(len(handshake)) + handshake + b"\x01\x00")
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        packet_length = await asyncio.wait_for(read_varint(reader), timeout=timeout)
        if packet_length < 2 or packet_length > MAX_JSON_BYTES + 10:
            raise MinecraftQueryError("The Java status packet length was invalid.")
        packet_id = await asyncio.wait_for(read_varint(reader), timeout=timeout)
        if packet_id != 0:
            raise MinecraftQueryError("The Java server returned an unexpected packet.")
        json_length = await asyncio.wait_for(read_varint(reader), timeout=timeout)
        if json_length < 0 or json_length > MAX_JSON_BYTES:
            raise MinecraftQueryError("The Java status JSON exceeded the size limit.")
        payload = await asyncio.wait_for(reader.readexactly(json_length), timeout=timeout)
    except (asyncio.TimeoutError, OSError, asyncio.IncompleteReadError) as exc:
        raise MinecraftQueryError("The Java server could not be reached or timed out.") from exc
    finally:
        if "writer" in locals():
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    result = parse_java_status(payload)
    result.update(
        edition="java",
        host=normalize_host(host),
        port=target_port,
        srv_target=target_host if target_host != normalize_host(host) else None,
        latency_ms=round((time.monotonic() - started) * 1000),
    )
    return result


def parse_bedrock_pong(payload: bytes) -> dict:
    if len(payload) > MAX_BEDROCK_BYTES:
        raise MinecraftQueryError("The Bedrock status response exceeded the size limit.")
    if len(payload) < 35 or payload[0] != 0x1C or payload[17:33] != BEDROCK_MAGIC:
        raise MinecraftQueryError("The Bedrock server returned an invalid unconnected pong.")
    length = struct.unpack(">H", payload[33:35])[0]
    if length > MAX_BEDROCK_BYTES - 35 or len(payload) != 35 + length:
        raise MinecraftQueryError("The Bedrock server returned an invalid status-string length.")
    try:
        fields = payload[35:].decode("utf-8").split(";")
    except UnicodeDecodeError as exc:
        raise MinecraftQueryError("The Bedrock server returned invalid status text.") from exc
    if len(fields) < 6 or fields[0] not in {"MCPE", "MCEE"}:
        raise MinecraftQueryError("The Bedrock server returned an unexpected status string.")
    try:
        players, maximum = int(fields[4]), int(fields[5])
    except ValueError as exc:
        raise MinecraftQueryError("The Bedrock server returned invalid player counts.") from exc
    return {
        "edition": "bedrock" if fields[0] == "MCPE" else "education",
        "motd": fields[1],
        "protocol": int(fields[2]) if fields[2].isdigit() else 0,
        "version": fields[3] or "Unknown",
        "players": players,
        "max_players": maximum,
        "motd_line_2": fields[7] if len(fields) > 7 else "",
        "game_mode": fields[8] if len(fields) > 8 else "",
    }


def _bedrock_query(family: int, protocol: int, sockaddr, timeout: float):
    timestamp = int(time.time() * 1000)
    client_guid = int.from_bytes(os.urandom(8), "big") & ((1 << 63) - 1)
    packet = b"\x01" + struct.pack(">q", timestamp) + BEDROCK_MAGIC + struct.pack(">q", client_guid)
    sock = socket.socket(family, socket.SOCK_DGRAM, protocol)
    try:
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        started = time.monotonic()
        sock.send(packet)
        payload = sock.recv(MAX_BEDROCK_BYTES + 1)
        latency_ms = round((time.monotonic() - started) * 1000)
    except socket.timeout as exc:
        raise MinecraftQueryError("The Bedrock server query timed out.") from exc
    except OSError as exc:
        raise MinecraftQueryError("The Bedrock server could not be reached.") from exc
    finally:
        sock.close()
    return payload, latency_ms


async def query_bedrock(
    host: str,
    port: int = 19132,
    *,
    private_hosts: Iterable[str] = (),
    timeout: float = 3.0,
) -> dict:
    host = normalize_host(host)
    family, protocol, sockaddr = await resolve_socket_target(
        host,
        port,
        policy_host=host,
        private_hosts=private_hosts,
        socktype=socket.SOCK_DGRAM,
        timeout=timeout,
    )
    try:
        payload, latency_ms = await asyncio.wait_for(
            asyncio.to_thread(_bedrock_query, family, protocol, sockaddr, timeout),
            timeout=timeout + 0.5,
        )
    except asyncio.TimeoutError as exc:
        raise MinecraftQueryError("The Bedrock server query timed out.") from exc
    result = parse_bedrock_pong(payload)
    result.update(host=host, port=int(port), latency_ms=latency_ms)
    return result

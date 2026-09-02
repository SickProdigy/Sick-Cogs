import argparse
import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from aiohttp import WSMsgType, web


PAIRING_LIFETIME_SECONDS = 10 * 60
AUTH_WINDOW_SECONDS = 5 * 60
MAX_MESSAGE_BYTES = 64 * 1024
MAX_NONCES_PER_INSTALLATION = 500
PROTOCOL_VERSION = 1


def _required_absolute_path(name: str) -> Path:
    value = os.environ.get(name, "")
    path = Path(value)
    if not value or not path.is_absolute():
        raise RuntimeError(f"{name} must be an absolute path.")
    return path


def _database_path() -> Path:
    path = _required_absolute_path("SICKWALLET_BROKER_DATABASE")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_database_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_database() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS pairing_codes (
                digest TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL,
                consumed_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS installations (
                installation_id TEXT PRIMARY KEY,
                credential TEXT NOT NULL,
                deployment_id TEXT NOT NULL,
                discord_application_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                revoked_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS connection_nonces (
                installation_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                seen_at INTEGER NOT NULL,
                PRIMARY KEY (installation_id, nonce),
                FOREIGN KEY (installation_id)
                    REFERENCES installations(installation_id) ON DELETE CASCADE
            );
            """
        )
        connection.commit()


def create_pairing_code() -> tuple[str, int]:
    initialize_database()
    code = secrets.token_urlsafe(24)
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    expires_at = int(time.time()) + PAIRING_LIFETIME_SECONDS
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM pairing_codes WHERE expires_at <= ? OR consumed_at IS NOT NULL",
            (int(time.time()),),
        )
        connection.execute(
            "INSERT INTO pairing_codes (digest, expires_at) VALUES (?, ?)",
            (digest, expires_at),
        )
        connection.commit()
    return code, expires_at


def _consume_pairing_code(code: str, deployment_id: str, application_id: str) -> dict | None:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    now = int(time.time())
    installation_id = secrets.token_urlsafe(18)
    credential = secrets.token_urlsafe(32)
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT expires_at, consumed_at
            FROM pairing_codes
            WHERE digest = ?
            """,
            (digest,),
        ).fetchone()
        if row is None or row["consumed_at"] is not None or int(row["expires_at"]) <= now:
            connection.rollback()
            return None
        connection.execute(
            "UPDATE pairing_codes SET consumed_at = ? WHERE digest = ?",
            (now, digest),
        )
        connection.execute(
            """
            INSERT INTO installations (
                installation_id,
                credential,
                deployment_id,
                discord_application_id,
                created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (installation_id, credential, deployment_id, application_id, now),
        )
        connection.commit()
    return {
        "version": PROTOCOL_VERSION,
        "installation_id": installation_id,
        "credential": credential,
        "deployment_id": deployment_id,
        "discord_application_id": application_id,
    }


def _canonical_connection(
    timestamp: str, nonce: str, installation_id: str
) -> bytes:
    return "\n".join(
        (
            "sickwallet-ws-v1",
            timestamp,
            nonce,
            installation_id,
            "GET",
            "/v1/socket",
        )
    ).encode("utf-8")


def _authenticate_connection(request: web.Request) -> tuple[bool, str, str]:
    installation_id = request.headers.get("X-SickWallet-Installation", "")
    timestamp = request.headers.get("X-SickWallet-Timestamp", "")
    nonce = request.headers.get("X-SickWallet-Nonce", "")
    signature = request.headers.get("X-SickWallet-Signature", "").casefold()
    if (
        not installation_id
        or not timestamp.isdigit()
        or not 16 <= len(nonce) <= 128
        or len(signature) != 64
    ):
        return False, "invalid_authentication", ""
    now = int(time.time())
    if abs(now - int(timestamp)) > AUTH_WINDOW_SECONDS:
        return False, "connection_expired", ""
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        installation = connection.execute(
            """
            SELECT credential
            FROM installations
            WHERE installation_id = ? AND revoked_at IS NULL
            """,
            (installation_id,),
        ).fetchone()
        if installation is None:
            connection.rollback()
            return False, "invalid_authentication", ""
        expected = hmac.new(
            str(installation["credential"]).encode("utf-8"),
            _canonical_connection(timestamp, nonce, installation_id),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            connection.rollback()
            return False, "invalid_authentication", ""
        connection.execute(
            "DELETE FROM connection_nonces WHERE seen_at < ?",
            (now - AUTH_WINDOW_SECONDS,),
        )
        try:
            connection.execute(
                """
                INSERT INTO connection_nonces (installation_id, nonce, seen_at)
                VALUES (?, ?, ?)
                """,
                (installation_id, nonce, now),
            )
        except sqlite3.IntegrityError:
            connection.rollback()
            return False, "connection_replayed", ""
        rows = connection.execute(
            """
            SELECT nonce FROM connection_nonces
            WHERE installation_id = ?
            ORDER BY seen_at DESC
            LIMIT -1 OFFSET ?
            """,
            (installation_id, MAX_NONCES_PER_INSTALLATION),
        ).fetchall()
        if rows:
            connection.executemany(
                """
                DELETE FROM connection_nonces
                WHERE installation_id = ? AND nonce = ?
                """,
                [(installation_id, row["nonce"]) for row in rows],
            )
        connection.commit()
    return True, "ok", installation_id


class Broker:
    def __init__(self):
        self.connections: dict[str, web.WebSocketResponse] = {}
        self.connection_lock = asyncio.Lock()

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "sickwallet-broker",
                "version": PROTOCOL_VERSION,
                "connected_installations": len(self.connections),
            }
        )

    async def pair(self, request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response(
                {"error": {"code": "invalid_request", "message": "JSON is required."}},
                status=415,
            )
        try:
            payload = await request.json()
            code = str(payload["code"]).strip()
            deployment_id = str(payload["deployment_id"]).strip()
            application_id = str(payload["discord_application_id"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return web.json_response(
                {"error": {"code": "invalid_request", "message": "Pairing data is invalid."}},
                status=400,
            )
        if not code or not deployment_id or not application_id:
            return web.json_response(
                {"error": {"code": "invalid_request", "message": "Pairing data is incomplete."}},
                status=400,
            )
        result = _consume_pairing_code(code, deployment_id, application_id)
        if result is None:
            return web.json_response(
                {"error": {"code": "pairing_rejected", "message": "The pairing code is invalid or expired."}},
                status=403,
            )
        return web.json_response({"data": result}, status=201)

    async def socket(self, request: web.Request) -> web.StreamResponse:
        authenticated, code, installation_id = _authenticate_connection(request)
        if not authenticated:
            return web.json_response(
                {"error": {"code": code, "message": "WebSocket authentication failed."}},
                status=401,
            )
        socket = web.WebSocketResponse(
            heartbeat=30,
            max_msg_size=MAX_MESSAGE_BYTES,
            autoping=True,
        )
        await socket.prepare(request)
        async with self.connection_lock:
            previous = self.connections.get(installation_id)
            self.connections[installation_id] = socket
            if previous is not None and not previous.closed:
                await previous.close(code=4001, message=b"Replaced by a newer connection")
        await socket.send_json(
            {
                "type": "welcome",
                "version": PROTOCOL_VERSION,
                "installation_id": installation_id,
                "connected_at": int(time.time()),
            }
        )
        try:
            async for message in socket:
                if message.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        await socket.close(code=4002, message=b"Invalid JSON")
                        break
                    if payload == {"type": "ping"}:
                        await socket.send_json({"type": "pong", "at": int(time.time())})
                    else:
                        await socket.send_json(
                            {
                                "type": "error",
                                "code": "unsupported_message",
                            }
                        )
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            async with self.connection_lock:
                if self.connections.get(installation_id) is socket:
                    del self.connections[installation_id]
        return socket


def create_app() -> web.Application:
    initialize_database()
    broker = Broker()
    app = web.Application(client_max_size=MAX_MESSAGE_BYTES)
    app.router.add_get("/health", broker.health)
    app.router.add_post("/v1/pair", broker.pair)
    app.router.add_get("/v1/socket", broker.socket)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="SickWallet website transport broker")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("pair-code", help="Create a one-time cog pairing code")
    serve = subcommands.add_parser("serve", help="Run the loopback broker")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()

    if args.command == "pair-code":
        code, expires_at = create_pairing_code()
        print(code)
        print(f"Expires at Unix time {expires_at}")
        return

    web.run_app(
        create_app(),
        host=args.host,
        port=args.port,
        access_log=None,
        print=None,
    )


if __name__ == "__main__":
    main()

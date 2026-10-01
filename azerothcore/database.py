import asyncio
import re
import ssl
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import aiomysql
except ImportError:  # pragma: no cover - Red installs requirements before loading the cog.
    aiomysql = None


TOKEN_NAMESPACE = "azerothcore_mysql"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
_TRUE_VALUES = {"1", "true", "yes", "on", "required", "verify"}
RACE_NAMES = {
    1: "Human", 2: "Orc", 3: "Dwarf", 4: "Night Elf", 5: "Undead",
    6: "Tauren", 7: "Gnome", 8: "Troll", 10: "Blood Elf", 11: "Draenei",
}
CLASS_NAMES = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest",
    6: "Death Knight", 7: "Shaman", 8: "Mage", 9: "Warlock", 11: "Druid",
}
LEADERBOARD_COLUMNS = {
    "level": "c.`level`",
    "gold": "c.`money`",
    "kills": "c.`totalKills`",
    "quests": "COALESCE(q.`quest_count`, 0)",
}


class DatabaseConfigurationError(RuntimeError):
    pass


class DatabaseUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    user: str
    password: str
    characters_database: str
    auth_database: str
    playerbots_database: Optional[str]
    connect_timeout: int
    tls: bool
    tls_ca: Optional[str]

    @classmethod
    def from_tokens(cls, tokens: Mapping[str, str]) -> "DatabaseSettings":
        required = ("host", "user", "password", "characters_database", "auth_database")
        missing = [key for key in required if not str(tokens.get(key, "")).strip()]
        if missing:
            raise DatabaseConfigurationError(
                "MySQL is missing required shared-token fields: " + ", ".join(missing) + "."
            )
        try:
            port = int(tokens.get("port", 3306))
            timeout = int(tokens.get("connect_timeout", 10))
        except (TypeError, ValueError) as exc:
            raise DatabaseConfigurationError("MySQL port and connect_timeout must be integers.") from exc
        if not 1 <= port <= 65535:
            raise DatabaseConfigurationError("MySQL port must be between 1 and 65535.")
        if not 3 <= timeout <= 60:
            raise DatabaseConfigurationError("MySQL connect_timeout must be between 3 and 60 seconds.")

        databases = {
            "characters_database": str(tokens["characters_database"]).strip(),
            "auth_database": str(tokens["auth_database"]).strip(),
            "playerbots_database": str(tokens.get("playerbots_database", "")).strip() or None,
        }
        for label, value in databases.items():
            if value is not None and not _IDENTIFIER_RE.fullmatch(value):
                raise DatabaseConfigurationError(
                    f"{label} may contain only letters, numbers, and underscores."
                )

        tls = str(tokens.get("tls", "false")).strip().casefold() in _TRUE_VALUES
        tls_ca = str(tokens.get("tls_ca", "")).strip() or None
        if tls_ca and not tls:
            raise DatabaseConfigurationError("Set tls=true when tls_ca is configured.")
        return cls(
            host=str(tokens["host"]).strip(),
            port=port,
            user=str(tokens["user"]).strip(),
            password=str(tokens["password"]),
            characters_database=databases["characters_database"],
            auth_database=databases["auth_database"],
            playerbots_database=databases["playerbots_database"],
            connect_timeout=timeout,
            tls=tls,
            tls_ca=tls_ca,
        )

    def ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self.tls:
            return None
        return ssl.create_default_context(cafile=self.tls_ca)

    def fingerprint(self) -> Tuple[Any, ...]:
        return (
            self.host, self.port, self.user, self.password, self.characters_database,
            self.auth_database, self.playerbots_database, self.connect_timeout,
            self.tls, self.tls_ca,
        )


@dataclass(frozen=True)
class CharacterRecord:
    guid: int
    name: str
    race: int
    character_class: int
    gender: int
    level: int
    online: bool
    total_time: int
    money: int
    total_kills: int
    quests: int
    account_name: str
    account_type: Optional[int]
    player_type: str

    @property
    def race_name(self) -> str:
        return RACE_NAMES.get(self.race, f"Race {self.race}")

    @property
    def class_name(self) -> str:
        return CLASS_NAMES.get(self.character_class, f"Class {self.character_class}")


class AzerothDatabase:
    def __init__(
        self,
        token_getter: Callable[[], Awaitable[Mapping[str, str]]],
        prefix_getter: Callable[[], Awaitable[Sequence[str]]],
    ):
        self._token_getter = token_getter
        self._prefix_getter = prefix_getter
        self._pool = None
        self._pool_fingerprint: Optional[Tuple[Any, ...]] = None
        self._pool_lock = asyncio.Lock()

    async def is_configured(self) -> bool:
        tokens = await self._token_getter()
        return all(
            str(tokens.get(key, "")).strip()
            for key in ("host", "user", "password", "characters_database", "auth_database")
        )

    async def settings(self) -> DatabaseSettings:
        return DatabaseSettings.from_tokens(await self._token_getter())

    async def close(self) -> None:
        async with self._pool_lock:
            if self._pool is not None:
                self._pool.close()
                await self._pool.wait_closed()
                self._pool = None
                self._pool_fingerprint = None

    async def _get_pool(self):
        if aiomysql is None:
            raise DatabaseUnavailable("The aiomysql dependency is not installed.")
        settings = await self.settings()
        fingerprint = settings.fingerprint()
        async with self._pool_lock:
            if self._pool is not None and self._pool_fingerprint != fingerprint:
                self._pool.close()
                await self._pool.wait_closed()
                self._pool = None
            if self._pool is None:
                try:
                    self._pool = await aiomysql.create_pool(
                        host=settings.host,
                        port=settings.port,
                        user=settings.user,
                        password=settings.password,
                        minsize=1,
                        maxsize=3,
                        autocommit=True,
                        connect_timeout=settings.connect_timeout,
                        pool_recycle=300,
                        charset="utf8mb4",
                        ssl=settings.ssl_context(),
                    )
                except Exception as exc:
                    raise DatabaseUnavailable(
                        "Could not connect to the configured AzerothCore database. "
                        "Check the host, port, read-only credentials, firewall, and TLS settings."
                    ) from exc
                self._pool_fingerprint = fingerprint
            return self._pool

    async def _query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        settings = await self.settings()
        pool = await self._get_pool()
        try:
            async with pool.acquire() as connection:
                async with connection.cursor(aiomysql.DictCursor) as cursor:
                    await asyncio.wait_for(
                        cursor.execute(sql, tuple(params)), timeout=settings.connect_timeout
                    )
                    rows = await asyncio.wait_for(
                        cursor.fetchall(), timeout=settings.connect_timeout
                    )
                    return [dict(row) for row in rows]
        except asyncio.TimeoutError as exc:
            raise DatabaseUnavailable("The AzerothCore database query timed out.") from exc
        except Exception as exc:
            raise DatabaseUnavailable(
                "The AzerothCore database query failed. Verify the read-only grants and schema names."
            ) from exc

    @staticmethod
    def classify_player(account_name: str, account_type: Optional[int], prefixes: Sequence[str]) -> str:
        folded = (account_name or "").casefold()
        prefix_match = any(folded.startswith(prefix.casefold()) for prefix in prefixes if prefix)
        if prefix_match and account_type == 1:
            return "random_ai"
        if prefix_match and account_type == 2:
            return "account_ai"
        if prefix_match or account_type in {1, 2}:
            return "unknown_ai"
        return "human"

    @staticmethod
    def _quoted(name: str) -> str:
        if not _IDENTIFIER_RE.fullmatch(name):
            raise DatabaseConfigurationError(
                "Database names may contain only letters, numbers, and underscores."
            )
        return f"`{name}`"

    def _select_sql(self, settings: DatabaseSettings, *, where: str, order: str = "") -> str:
        characters = self._quoted(settings.characters_database)
        auth = self._quoted(settings.auth_database)
        if settings.playerbots_database:
            playerbots = self._quoted(settings.playerbots_database)
            account_type = "pat.`account_type`"
            playerbots_join = (
                f"LEFT JOIN {playerbots}.`playerbots_account_type` pat "
                "ON pat.`account_id` = c.`account`"
            )
        else:
            account_type = "NULL"
            playerbots_join = ""
        return f"""
            SELECT c.`guid`, c.`name`, c.`race`, c.`class`, c.`gender`, c.`level`,
                   c.`online`, c.`totaltime`, c.`money`, c.`totalKills`,
                   COALESCE(q.`quest_count`, 0) AS `quest_count`,
                   a.`username` AS `account_name`, {account_type} AS `account_type`
            FROM {characters}.`characters` c
            INNER JOIN {auth}.`account` a ON a.`id` = c.`account`
            LEFT JOIN (
                SELECT `guid`, COUNT(`guid`) AS `quest_count`
                FROM {characters}.`character_queststatus_rewarded`
                WHERE `active` = 1
                GROUP BY `guid`
            ) q ON q.`guid` = c.`guid`
            {playerbots_join}
            WHERE c.`deleteDate` IS NULL AND ({where})
            {order}
        """

    async def _records(self, rows: Sequence[Mapping[str, Any]]) -> List[CharacterRecord]:
        prefixes = await self._prefix_getter()
        records = []
        for row in rows:
            account_name = str(row.get("account_name") or "")
            account_type = row.get("account_type")
            account_type = int(account_type) if account_type is not None else None
            records.append(CharacterRecord(
                guid=int(row.get("guid") or 0),
                name=str(row.get("name") or "Unknown"),
                race=int(row.get("race") or 0),
                character_class=int(row.get("class") or 0),
                gender=int(row.get("gender") or 0),
                level=int(row.get("level") or 0),
                online=bool(row.get("online")),
                total_time=int(row.get("totaltime") or 0),
                money=int(row.get("money") or 0),
                total_kills=int(row.get("totalKills") or 0),
                quests=int(row.get("quest_count") or 0),
                account_name=account_name,
                account_type=account_type,
                player_type=self.classify_player(account_name, account_type, prefixes),
            ))
        return records

    async def check(self) -> Dict[str, Any]:
        settings = await self.settings()
        characters = self._quoted(settings.characters_database)
        rows = await self._query(f"SELECT COUNT(`guid`) AS `characters` FROM {characters}.`characters`")
        return {"characters": int(rows[0]["characters"]), "tls": settings.tls}

    async def character(self, name: str) -> Optional[CharacterRecord]:
        settings = await self.settings()
        sql = self._select_sql(settings, where="LOWER(c.`name`) = LOWER(%s)", order="LIMIT 1")
        records = await self._records(await self._query(sql, (name,)))
        return records[0] if records else None

    async def online(self, audience: str, limit: int = 100) -> List[CharacterRecord]:
        if audience not in {"humans", "ai"}:
            raise ValueError("Audience must be humans or ai.")
        settings = await self.settings()
        prefixes = await self._prefix_getter()
        audience_sql, params = self._audience_filter(settings, audience, prefixes)
        limit = max(1, min(int(limit), 100))
        sql = self._select_sql(
            settings,
            where=f"c.`online` = 1 AND {audience_sql}",
            order="ORDER BY c.`name` LIMIT %s",
        )
        return await self._records(await self._query(sql, (*params, limit)))

    @staticmethod
    def _audience_filter(
        settings: DatabaseSettings, audience: str, prefixes: Sequence[str]
    ) -> Tuple[str, List[str]]:
        signals = ["LOWER(a.`username`) LIKE LOWER(%s)" for prefix in prefixes if prefix]
        params = [f"{prefix}%" for prefix in prefixes if prefix]
        if settings.playerbots_database:
            signals.append("pat.`account_type` IN (1, 2)")
        if not signals:
            return ("1 = 0", []) if audience == "ai" else ("1 = 1", [])
        combined = " OR ".join(signals)
        return (f"NOT ({combined})", params) if audience == "humans" else (f"({combined})", params)

    async def leaderboard(
        self, stat: str, audience: str = "humans", limit: int = 10
    ) -> List[CharacterRecord]:
        if stat not in LEADERBOARD_COLUMNS:
            raise ValueError("Unknown leaderboard statistic.")
        if audience not in {"humans", "ai"}:
            raise ValueError("Audience must be humans or ai.")
        settings = await self.settings()
        prefixes = await self._prefix_getter()
        audience_sql, params = self._audience_filter(settings, audience, prefixes)
        limit = max(1, min(int(limit), 25))
        column = LEADERBOARD_COLUMNS[stat]
        sql = self._select_sql(
            settings,
            where=f"{column} > 0 AND {audience_sql}",
            order=f"ORDER BY {column} DESC, c.`name` ASC LIMIT %s",
        )
        rows = await self._query(sql, (*params, limit))
        return await self._records(rows)

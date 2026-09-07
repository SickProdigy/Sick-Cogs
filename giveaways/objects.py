import random
from datetime import datetime, timezone

import discord
from redbot.core import bank


class GiveawayError(Exception):
    def __init__(self, message: str):
        self.message = message


class GiveawayEnterError(GiveawayError):
    pass


class AlreadyEnteredError(GiveawayError):
    pass


class Giveaway:
    def __init__(
        self,
        guildid: int,
        channelid: int,
        messageid: int,
        endtime: datetime,
        prize: str = None,
        emoji: str = "🎉",
        ended: bool = False,
        *,
        entrants=None,
        **kwargs,
    ) -> None:
        self.guildid = guildid
        self.channelid = channelid
        self.messageid = messageid
        self.endtime = endtime
        self.prize = prize
        self.entrants = entrants or []
        self.emoji = emoji
        self.ended = ended
        self.kwargs = kwargs

    @classmethod
    def from_dict(cls, data: dict) -> "Giveaway":
        endtime = data["endtime"]
        if isinstance(endtime, str):
            endtime = datetime.fromisoformat(endtime)
        if endtime.tzinfo is None:
            endtime = endtime.replace(tzinfo=timezone.utc)
        return cls(
            guildid=int(data["guildid"]),
            channelid=int(data["channelid"]),
            messageid=int(data["messageid"]),
            endtime=endtime,
            prize=data.get("prize"),
            emoji=data.get("emoji") or "🎉",
            ended=bool(data.get("ended", False)),
            entrants=[int(user_id) for user_id in data.get("entrants") or []],
            **dict(data.get("kwargs") or {}),
        )

    def to_dict(self) -> dict:
        return {
            "guildid": self.guildid,
            "channelid": self.channelid,
            "messageid": self.messageid,
            "endtime": self.endtime.isoformat(),
            "prize": self.prize,
            "entrants": list(self.entrants),
            "emoji": self.emoji,
            "ended": self.ended,
            "kwargs": dict(self.kwargs),
        }

    async def add_entrant(self, user: discord.Member, *, cog) -> None:
        if not self.kwargs.get("multientry", False) and user.id in self.entrants:
            raise AlreadyEnteredError("You have already entered this giveaway.")
        bypass = self.does_entrant_bypass(user)
        if bypass is False:
            if self.kwargs.get("roles", []) and all(
                int(role) not in [x.id for x in user.roles]
                for role in self.kwargs.get("roles", [])
            ):
                raise GiveawayEnterError(
                    "You do not have the required roles to join this giveaway."
                )

            if self.kwargs.get("blacklist", []) and any(
                int(role) in [x.id for x in user.roles]
                for role in self.kwargs.get("blacklist", [])
            ):
                raise GiveawayEnterError("Your role is blacklisted from this giveaway.")
            if self.kwargs.get("joined") is not None and user.joined_at is None:
                raise GiveawayEnterError("Your server join date is unavailable.")
            if (
                self.kwargs.get("joined", None) is not None
                and (datetime.now(timezone.utc) - user.joined_at.replace(tzinfo=timezone.utc)).days
                < self.kwargs["joined"]
            ):
                raise GiveawayEnterError(
                    f"Your account is too new to join this giveaway. You must have joined {self.kwargs['joined']} days ago."
                )
            if (
                self.kwargs.get("created", None) is not None
                and (
                    datetime.now(timezone.utc) - user.created_at.replace(tzinfo=timezone.utc)
                ).days
                < self.kwargs["created"]
            ):
                raise GiveawayEnterError(
                    f"Your account is too new to join this giveaway. You must have created your account {self.kwargs['created']} days ago."
                )
            if self.kwargs.get("cost", None) is not None:
                if not await bank.can_spend(user, self.kwargs["cost"]):
                    raise GiveawayEnterError(
                        "You do not have enough credits to join this giveaway."
                    )

                await bank.withdraw_credits(user, self.kwargs["cost"])
        self.entrants.append(user.id)
        if self.kwargs.get("multi", None) is not None and any(
            int(role) in [x.id for x in user.roles] for role in self.kwargs.get("multi-roles", [])
        ):
            for _ in range(self.kwargs["multi"] - 1):
                self.entrants.append(user.id)
        await cog.config.custom("giveaways", self.guildid, self.messageid).entrants.set(
            self.entrants
        )
        return

    def remove_entrant(self, userid: int) -> None:
        self.entrants = [x for x in self.entrants if x != userid]

    def draw_winner(self, valid_user_ids=None):
        winner_count = self.kwargs.get("winners") or 1
        weighted_entrants = {}
        for user_id in self.entrants:
            if valid_user_ids is not None and user_id not in valid_user_ids:
                continue
            weighted_entrants[user_id] = weighted_entrants.get(user_id, 0) + 1
        if len(weighted_entrants) < winner_count:
            return None

        winners = []
        for _ in range(winner_count):
            user_ids = list(weighted_entrants)
            weights = [weighted_entrants[user_id] for user_id in user_ids]
            winner = random.choices(user_ids, weights=weights, k=1)[0]
            winners.append(winner)
            del weighted_entrants[winner]
        return winners

    def does_entrant_bypass(self, user: discord.Member) -> bool:
        if not self.kwargs.get("bypass-roles", []):
            return False
        bypass_type = self.kwargs.get("bypass-type")
        if bypass_type == "or":
            return any(
                list(
                    int(role) in [x.id for x in user.roles]
                    for role in self.kwargs.get("bypass-roles")
                )
            )
        elif bypass_type == "and":
            return all(
                list(
                    int(role) in [x.id for x in user.roles]
                    for role in self.kwargs.get("bypass-roles")
                )
            )
        else:
            return False

    def __str__(self) -> str:
        return f"{self.prize} - {self.endtime}"

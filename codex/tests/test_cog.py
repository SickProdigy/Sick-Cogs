import unittest

from codex.codex import Codex, DEFAULT_USER


class Value:
    def __init__(self, group, key):
        self.group = group
        self.key = key

    async def __call__(self):
        return self.group.data[self.key]

    async def set(self, value):
        self.group.data[self.key] = value


class Group:
    def __init__(self, data=None):
        self.data = dict(DEFAULT_USER)
        if data:
            self.data.update(data)

    def __getattr__(self, key):
        if key in self.data:
            return Value(self, key)
        raise AttributeError(key)

    async def all(self):
        return dict(self.data)

    async def clear(self):
        self.data = dict(DEFAULT_USER)


class Config:
    def __init__(self, users):
        self.users = {int(key): Group(value) for key, value in users.items()}

    def user_from_id(self, user_id):
        return self.users.setdefault(int(user_id), Group())

    async def all_users(self):
        return {key: dict(group.data) for key, group in self.users.items()}


class User:
    def __init__(self, user_id):
        self.id = user_id
        self.messages = []

    async def send(self, content=None, embed=None, view=None):
        self.messages.append(content)
        return object()


class Bot:
    def __init__(self, users):
        self.users = {user.id: user for user in users}

    def get_user(self, user_id):
        return self.users.get(user_id)

    async def fetch_user(self, user_id):
        return self.users.get(user_id)


class Manager:
    def __init__(self, payload):
        self.payload = payload
        self.removed = []

    async def rate_limits(self, user_id):
        return self.payload

    async def logout(self, user_id):
        return None

    def remove_account(self, user_id):
        self.removed.append(user_id)


class CogTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, records, payload):
        users = [User(user_id) for user_id in records]
        cog = Codex.__new__(Codex)
        cog.config = Config(records)
        cog.bot = Bot(users)
        cog.manager = Manager(payload)
        return cog

    async def test_low_warning_is_private_and_idempotent(self):
        payload = {
            "rateLimits": {
                "limitId": "codex",
                "limitName": "Codex",
                "secondary": {
                    "usedPercent": 90,
                    "resetsAt": 500,
                    "windowDurationMins": 10080,
                },
            }
        }
        cog = self.cog(
            {
                1: {
                    "connected": True,
                    "enabled": True,
                    "low_threshold": 20,
                },
                2: {
                    "connected": True,
                    "enabled": True,
                    "low_threshold": 5,
                },
            },
            payload,
        )
        first = await cog.config.user_from_id(1).all()
        self.assertEqual(await cog.process_user(1, first), 1)
        self.assertEqual(len(cog.bot.users[1].messages), 1)
        self.assertEqual(cog.bot.users[2].messages, [])
        first = await cog.config.user_from_id(1).all()
        self.assertEqual(await cog.process_user(1, first), 0)

    async def test_user_deletion_removes_only_target_credentials(self):
        cog = self.cog(
            {1: {"connected": True}, 2: {"connected": True}}, {}
        )
        await cog.red_delete_data_for_user(
            requester="discord_deleted_user", user_id=1
        )
        self.assertEqual(cog.manager.removed, [1])
        self.assertFalse(
            (await cog.config.user_from_id(1).all())["connected"]
        )
        self.assertTrue(
            (await cog.config.user_from_id(2).all())["connected"]
        )


if __name__ == "__main__":
    unittest.main()

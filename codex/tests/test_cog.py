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

    async def send(self, message):
        self.messages.append(message)


class Bot:
    def __init__(self, users):
        self.users = {user.id: user for user in users}

    def get_user(self, user_id):
        return self.users.get(user_id)

    async def fetch_user(self, user_id):
        return self.users.get(user_id)


class CogTests(unittest.IsolatedAsyncioTestCase):
    def cog(self, records):
        users = [User(user_id) for user_id in records]
        cog = Codex.__new__(Codex)
        cog.config = Config(records)
        cog.bot = Bot(users)
        return cog

    async def test_due_reminder_is_private_and_idempotent(self):
        cog = self.cog({
            1: {
                "enabled": True,
                "reset_at": 100,
                "reminder_offsets": [20],
                "remaining_percent": 40,
            },
            2: {
                "enabled": True,
                "reset_at": 200,
                "reminder_offsets": [20],
                "remaining_percent": 90,
            },
        })
        data = await cog.config.user_from_id(1).all()
        self.assertEqual(await cog.process_user(1, data, now=90), 1)
        self.assertEqual(len(cog.bot.users[1].messages), 1)
        self.assertEqual(cog.bot.users[2].messages, [])
        data = await cog.config.user_from_id(1).all()
        self.assertEqual(await cog.process_user(1, data, now=90), 0)
        self.assertEqual(len(cog.bot.users[1].messages), 1)

    async def test_cycle_reset_clears_estimate_and_advances(self):
        cog = self.cog({
            1: {
                "enabled": True,
                "reset_at": 100,
                "cycle_seconds": 50,
                "remaining_percent": 30,
                "updated_at": 80,
                "sent_keys": ["100:20"],
            }
        })
        data = await cog.config.user_from_id(1).all()
        await cog.process_user(1, data, now=125)
        updated = await cog.config.user_from_id(1).all()
        self.assertEqual(updated["reset_at"], 150)
        self.assertIsNone(updated["remaining_percent"])
        self.assertEqual(updated["sent_keys"], [])
        self.assertIn("30%", cog.bot.users[1].messages[0])

    async def test_user_deletion_clears_only_target_user(self):
        cog = self.cog({
            1: {"enabled": True, "remaining_percent": 20},
            2: {"enabled": True, "remaining_percent": 80},
        })
        await cog.red_delete_data_for_user(
            requester="discord_deleted_user", user_id=1
        )
        self.assertFalse((await cog.config.user_from_id(1).all())["enabled"])
        self.assertEqual(
            (await cog.config.user_from_id(2).all())["remaining_percent"], 80
        )


if __name__ == "__main__":
    unittest.main()

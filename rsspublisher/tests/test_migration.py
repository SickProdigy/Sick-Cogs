import unittest

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("rsspublisher_models", Path(__file__).parents[1] / "models.py")
_models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_models)
migrate_feed_data = _models.migrate_feed_data


class RSSMigrationTests(unittest.TestCase):
    def test_legacy_delivery_markers_are_preserved(self):
        data = {"url": "https://example.test/feed", "last_entry_id": "guid-1", "last_link": "https://example.test/1", "last_time": 123}
        migrated, _ = migrate_feed_data(data)
        self.assertEqual(migrated["last_entry_id"], "guid-1")
        self.assertEqual(migrated["last_link"], "https://example.test/1")
        self.assertEqual(migrated["last_time"], 123)
        self.assertIn("paused", migrated)

    def test_new_fields_are_added_without_replacing_existing_template(self):
        migrated, _ = migrate_feed_data({"url": "https://example.test/feed", "template": "$title"})
        self.assertEqual(migrated["template"], "$title")
        self.assertIn("last_success_at", migrated)

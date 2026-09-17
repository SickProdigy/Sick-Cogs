from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from incidents.incidents import Incidents


class IncidentFormattingTests(unittest.TestCase):
    def test_cases_sort_newest_first_and_format(self):
        old = SimpleNamespace(case_number=1, action_type="ban", user="Old", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        new = SimpleNamespace(case_number=2, action_type="kick", user="New", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(Incidents.sorted_cases([old, new]), [new, old])
        self.assertIn("#2 Kick", Incidents.case_line(new))

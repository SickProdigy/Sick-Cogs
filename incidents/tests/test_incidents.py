from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from incidents.incidents import Incidents


def case(number, action, days_ago=0, user=123):
    return SimpleNamespace(
        case_number=number,
        action_type=action,
        user=user,
        last_known_username="FormerUser",
        moderator=456,
        reason="Testing",
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        until=None,
        channel=None,
        amended_by=None,
        modified_at=None,
    )


class IncidentFormattingTests(unittest.TestCase):
    def test_cases_sort_newest_first_and_format(self):
        old = case(1, "ban", 2)
        new = case(2, "kick", 1)
        self.assertEqual(Incidents.sorted_cases([old, new]), [new, old])
        self.assertIn("#2 Kick", Incidents.case_line(new))

    def test_unresolved_users_keep_id_and_last_name(self):
        text = Incidents.case_line(case(4, "ban", user=228710499888398336))
        self.assertIn("FormerUser", text)
        self.assertIn("228710499888398336", text)

    def test_user_id_parser_accepts_mentions_and_ids(self):
        expected = 228710499888398336
        self.assertEqual(Incidents.parse_user_id(str(expected)), expected)
        self.assertEqual(Incidents.parse_user_id(f"<@{expected}>"), expected)
        self.assertEqual(Incidents.parse_user_id(f"<@!{expected}>"), expected)
        self.assertIsNone(Incidents.parse_user_id("FormerUser"))

    def test_filter_combines_action_and_bounded_window(self):
        cases = [case(1, "ban", 2), case(2, "kick", 2), case(3, "ban", 50)]
        result = Incidents.filter_cases(cases, action="ban", days=30)
        self.assertEqual([item.case_number for item in result], [1])

    def test_unix_timestamps_are_supported(self):
        value = 1_700_000_000
        self.assertEqual(int(Incidents._utc(value).timestamp()), value)

    def test_case_embed_contains_recorded_details(self):
        embed = Incidents.case_embed(case(7, "tempban"), position=0, total=1)
        self.assertIn("Case #7", embed.title)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Testing", fields["Reason"])
        self.assertIn("FormerUser", fields["User"])

    def test_summary_reports_incident_ages(self):
        embed = Incidents.summary_embed([case(1, "ban", 3), case(2, "warning", 1)], 7)
        self.assertIn("Total cases", embed.description)
        age_field = next(field.value for field in embed.fields if field.name == "Time since last incident")
        self.assertIn("Ban", age_field)
        self.assertIn("Warning", age_field)

import unittest

from coc.coc import Coc


class CocAttackNotificationTests(unittest.TestCase):
    def setUp(self):
        self.cog = object.__new__(Coc)

    @staticmethod
    def attacks():
        return [
            {
                "is_friendly": True,
                "attacker_name": "Ricardo",
                "defender_name": "HASSAN.DAREAI",
                "stars": 2,
                "destruction": 66,
            },
            {
                "is_friendly": False,
                "attacker_name": "7RB_2",
                "defender_name": "intriago",
                "stars": 3,
                "destruction": 100,
            },
        ]

    def test_ascii_compact_output_is_unchanged(self):
        result = self.cog._build_war_attack_update_text(self.attacks())
        self.assertEqual(
            result,
            "🟢 Friendly Attack | Ricardo → HASSAN.DAREAI | ⭐⭐ | 66.00%\n"
            "🔴 Enemy Attack | 7RB_2 → intriago | ⭐⭐⭐ | 100.00%",
        )

    def test_rtl_defender_is_isolated_before_stars_and_percentage(self):
        attack = self.attacks()[0]
        attack["defender_name"] = "حسن"
        attack["destruction"] = 87
        result = self.cog._build_war_attack_update_text([attack])
        self.assertEqual(
            result,
            "🟢 Friendly Attack | Ricardo → \u2068حسن\u2069 | ⭐⭐ | 87.00%",
        )

    def test_rtl_attacker_and_defender_are_isolated_in_card(self):
        attack = self.attacks()[1]
        attack["attacker_name"] = "אוסקר"
        attack["defender_name"] = "حسن"
        war = {
            "type": "random",
            "clan": {"tag": "#AAA", "name": "Home"},
            "opponent": {"tag": "#BBB", "name": "Away"},
        }
        embed = self.cog._build_war_attack_update_embed(war, "#AAA", [attack])
        self.assertEqual(
            embed.fields[0].value,
            "\u2068אוסקר\u2069 → \u2068حسن\u2069 | ⭐⭐⭐ | 100.00%",
        )

    def test_embedded_bidi_controls_are_removed_then_name_is_isolated(self):
        self.assertEqual(Coc._format_attack_name("حسن\u202eABC"), "\u2068حسنABC\u2069")

    def test_friendly_and_enemy_api_fields_keep_the_same_mapping(self):
        war = {
            "preparationStartTime": "20260920T000000.000Z",
            "startTime": "20260921T000000.000Z",
            "endTime": "20260922T000000.000Z",
            "clan": {
                "tag": "#AAA",
                "name": "Home",
                "members": [{
                    "tag": "#A1",
                    "name": "Ricardo",
                    "attacks": [{
                        "defenderTag": "#B1", "order": 1, "stars": 2,
                        "destructionPercentage": 87, "duration": 120,
                    }],
                }],
            },
            "opponent": {
                "tag": "#BBB",
                "name": "Away",
                "members": [{
                    "tag": "#B1",
                    "name": "حسن",
                    "attacks": [{
                        "defenderTag": "#A1", "order": 2, "stars": 1,
                        "destructionPercentage": 59, "duration": 110,
                    }],
                }],
            },
        }
        friendly, enemy = list(Coc._iter_war_attacks(war, "#AAA"))
        self.assertEqual(
            (friendly["attacker_name"], friendly["defender_name"],
             friendly["stars"], friendly["destruction"], friendly["is_friendly"]),
            ("Ricardo", "حسن", 2, 87, True),
        )
        self.assertEqual(
            (enemy["attacker_name"], enemy["defender_name"],
             enemy["stars"], enemy["destruction"], enemy["is_friendly"]),
            ("حسن", "Ricardo", 1, 59, False),
        )

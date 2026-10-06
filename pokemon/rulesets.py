"""Versioned battle-mechanics policies independent of campaign content."""

from dataclasses import dataclass

from .data import effectiveness as standard_effectiveness


@dataclass(frozen=True)
class BattleRuleset:
    key: str
    mechanics_generation: int
    catch_experience: bool = True

    def effectiveness(self, move_type, defender_types):
        return standard_effectiveness(move_type, defender_types)

    @staticmethod
    def move_category(move):
        return move.category

    @staticmethod
    def critical_denominator(move, attacker_speed):
        return max(1, 24 - move.crit_rate * 4)

    @staticmethod
    def critical_multiplier():
        return 1.5

    @staticmethod
    def paralysis_speed(speed):
        return max(1, speed // 2)

    def experience_reward(self,species,level,*,trainer=False,caught=False):
        reward=max(1,int(species.base_experience)*int(level)//7)
        if trainer:reward=reward*3//2
        if caught:
            if not self.catch_experience:return 0
            reward=max(1,reward//2)
        return reward

    @staticmethod
    def status_immune(status,types):
        kinds=set(types)
        return ((status=="burn" and "fire" in kinds) or
                (status=="freeze" and "ice" in kinds) or
                (status=="paralysis" and "electric" in kinds) or
                (status=="poison" and bool(kinds & {"poison","steel"})))

    @staticmethod
    def status_duration(status,rng):
        if status=="sleep":return 1+rng.randrange(3)
        if status=="confusion":return 2+rng.randrange(4)
        return 0

    @staticmethod
    def residual_divisor(status):
        return 16 if status=="burn" else 8

    @staticmethod
    def burned_attack(attack):
        return max(1,attack//2)


STANDARD = BattleRuleset("standard", mechanics_generation=9, catch_experience=True)
RULESETS = {STANDARD.key: STANDARD}


def resolve_ruleset(key):
    return RULESETS.get(str(key or "standard").casefold(), STANDARD)

"""Versioned battle-mechanics policies independent of campaign content."""

from dataclasses import dataclass

from .data import effectiveness as standard_effectiveness

TYPE_CHARTS={"modern":standard_effectiveness}


@dataclass(frozen=True)
class BattleRuleset:
    key: str
    mechanics_generation: int
    catch_experience: bool = True
    type_chart: str = "modern"

    def effectiveness(self, move_type, defender_types):
        return TYPE_CHARTS[self.type_chart](move_type, defender_types)

    @staticmethod
    def move_category(move):
        return move.category

    def damage_stats(self,move):
        category=self.move_category(move)
        return ("special_attack","special_defense") if category=="special" else ("attack","defense")

    @staticmethod
    def effective_stage(stage,*,critical=False,offensive=False):
        stage=max(-6,min(6,int(stage)))
        if critical and ((offensive and stage<0) or (not offensive and stage>0)):return 0
        return stage

    @staticmethod
    def critical_denominator(move, attacker_speed):
        stages=(24,8,2,1)
        return stages[min(max(0,int(move.crit_rate)),len(stages)-1)]

    @staticmethod
    def critical_multiplier():
        return 1.5

    def damage_modifier(self,move_type,attacker_types,defender_types,critical,rng):
        modifier=self.effectiveness(move_type,defender_types)
        if modifier==0:return 0.0
        if move_type in attacker_types:modifier*=1.5
        if critical:modifier*=self.critical_multiplier()
        return modifier*(85+rng.randrange(16))/100

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

    @staticmethod
    def catch_status_multiplier(status):
        if status in {"sleep","freeze"}:return 2.5
        if status in {"paralysis","poison","burn"}:return 1.5
        return 1.0


STANDARD = BattleRuleset("standard", mechanics_generation=9, catch_experience=True, type_chart="modern")
RULESETS = {STANDARD.key: STANDARD}


def resolve_ruleset(key):
    return RULESETS.get(str(key or "standard").casefold(), STANDARD)

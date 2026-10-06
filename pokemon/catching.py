"""Generation-aware capture calculations independent of Discord UI."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Ball:
    key: str
    name: str
    modifier: float


BALLS = {
    "poke_ball": Ball("poke_ball","Poké Ball",1.0),
    "great_ball": Ball("great_ball","Great Ball",1.5),
    "ultra_ball": Ball("ultra_ball","Ultra Ball",2.0),
}


@dataclass(frozen=True)
class CatchResult:
    caught: bool
    shakes: int
    catch_value: int
    chance: float


def calculate_catch_value(species,max_hp,current_hp,status,ball,ruleset):
    maximum=max(1,int(max_hp));current=max(1,min(maximum,int(current_hp)))
    value=((3*maximum-2*current)*int(species.catch_rate)*ball.modifier)/(3*maximum)
    value*=ruleset.catch_status_multiplier(status)
    return max(1,int(value))


def attempt_catch(species,max_hp,current_hp,status,ball_key,ruleset,rng):
    ball=BALLS.get(ball_key,BALLS["poke_ball"])
    value=calculate_catch_value(species,max_hp,current_hp,status,ball,ruleset)
    if value>=255:return CatchResult(True,4,value,1.0)
    chance=max(0.0,min(1.0,value/255.0));threshold=int(65536*(chance**0.25))
    shakes=0
    for _ in range(4):
        if rng.randrange(65536)>=threshold:break
        shakes+=1
    return CatchResult(shakes==4,shakes,value,chance)

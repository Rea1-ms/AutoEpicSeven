from dataclasses import dataclass

from module.base.button import ButtonWrapper
from tasks.combat.assets import assets_combat_configs_element_altar as altar_elements
from tasks.combat.assets import assets_combat_configs_element_hunt as hunt_elements
from tasks.combat.assets.assets_combat_configs_entry import (
    ALTER_CHECK,
    HUNT,
    HUNT_CHECK,
    SPIRIT_ALTAR,
)
from tasks.combat.assets import assets_combat_configs_grade_altar as altar_grades
from tasks.combat.assets import assets_combat_configs_grade_hunt as hunt_grades


@dataclass(frozen=True)
class CombatPlan:
    name: str
    entry: ButtonWrapper
    stage_check: ButtonWrapper
    elements: dict[str, tuple[ButtonWrapper, ButtonWrapper]]
    grades: dict[str, ButtonWrapper]


ALTAR_PLAN = CombatPlan(
    name="SpiritAltar",
    entry=SPIRIT_ALTAR,
    stage_check=ALTER_CHECK,
    elements={
        "Dark": (altar_elements.DARK, altar_elements.DARK_SELECTED),
        "Light": (altar_elements.LIGHT, altar_elements.LIGHT_SELECTED),
        "Water": (altar_elements.WATER, altar_elements.WATER_SELECTED),
        "Fire": (altar_elements.FIRE, altar_elements.FIRE_SELECTED),
        "Nature": (altar_elements.NATURE, altar_elements.NATURE_SELECTED),
    },
    grades={
        "Pri": altar_grades.PRI,
        "Mid": altar_grades.MID,
        "High": altar_grades.HIGH,
        "Hell": altar_grades.HELL,
    },
)

HUNT_PLAN = CombatPlan(
    name="Hunt",
    entry=HUNT,
    stage_check=HUNT_CHECK,
    elements={
        "Dark": (hunt_elements.DARK, hunt_elements.DARK_SELECTED),
        "Light": (hunt_elements.LIGHT, hunt_elements.LIGHT_SELECTED),
        "Water": (hunt_elements.WATER, hunt_elements.WATER_SELECTED),
        "Fire": (hunt_elements.FIRE, hunt_elements.FIRE_SELECTED),
        "Nature": (hunt_elements.NATURE, hunt_elements.NATURE_SELECTED),
    },
    grades={
        "Mid": hunt_grades.MID,
        "High": hunt_grades.HIGH,
        "Hell": hunt_grades.HELL,
        "Dimensional": hunt_grades.DIMENSIONAL,
    },
)

COMBAT_PLANS = {
    "SpiritAltar": ALTAR_PLAN,
    "Hunt": HUNT_PLAN,
}

# ruff: noqa: E402
"""Historical manual assertions adapted to the account-free offline runner."""
import unittest
from module.config import server as _test_server
_test_server.set_lang("global_cn")


from types import SimpleNamespace


from tasks.dungeon.prepare import (
    CombatPrepare,
    CombatPrepareDigit,
    calculate_fast_combat_target,
)

from tasks.dungeon.dungeon import Combat


def repeat_budget(stamina, *, mode="Daily", leif_count=1):
    combat = Combat.__new__(Combat)
    combat.config = SimpleNamespace(
        task=SimpleNamespace(command="Combat"), Combat_Domain="Hunt",
        Combat_HuntGrade="Hell", Combat_BurnoutMode=mode,
        Combat_RepeatCombatLeifCount=leif_count,
    )
    target = combat._server_repeat_target_leif_count(stamina)
    return target, combat._server_repeat_stamina_budget(stamina, target, True)

class FastSupportProbe:
    def __init__(self, domain: str, grade: str = "Hell"):
        self.domain = domain
        self.grade = grade

    def _dungeon_domain(self):
        return self.domain

    def _combat_grade(self):
        return self.grade

class FastCountProbe(CombatPrepare):
    config = SimpleNamespace(Combat_FastCombatCount=20)

class FakeDevice:
    image = object()

    @staticmethod
    def app_is_running():
        return True

class FakePrepare(CombatPrepare):
    COMBAT_CHECK_SIMILARITY = 0.8

    def __init__(self, stamina: int, remaining: int = 10):
        self.device = FakeDevice()
        self.stamina = stamina
        self.remaining = remaining
        self.target = None

    def _is_prepare_page(self):
        return True

    def _handle_dungeon_additional(self):
        return False

    def _is_fast_combat_locked(self):
        return False

    def _ensure_fast_combat_state(self, enabled):
        return enabled

    def _ocr_fast_combat_remaining_times(self):
        return self.remaining

    def _ocr_combat_stamina(self):
        return self.stamina

    def _combat_stage_stamina_cost(self):
        return 20

    def _combat_fast_count(self):
        return 10

    def _set_prepare_count(self, target, *args, **kwargs):
        self.target = target
        return True

class FixedCountConfig:
    Emulator_PackageName = "CN-Official"
    UrgentTasks_Enable = False
    Combat_Domain = "Hunt"
    Combat_Element = "Water"
    Combat_HuntGrade = "Hell"
    Combat_FastCombat = True
    Combat_FastCombatCount = 10
    Combat_BurnoutMode = "Daily"
    task = SimpleNamespace(command="Combat")

    def __init__(self):
        self.delayed = False

    def task_call(self, *args, **kwargs):
        pass

    @staticmethod
    def cross_get(path, default=None):
        return default

    def task_delay(self, *args, **kwargs):
        self.delayed = True

class FixedCountRunProbe(Combat):
    def __init__(self):
        self.config = FixedCountConfig()
        self.device = FakeDevice()
        self.snapshot_stamina = iter((240, 40))
        self.repeat_prepare_called = False
        self.left_to_main = False

    def _prepare_background_repeat_check_context(self):
        pass

    def _adopt_existing_background_repeat_combat(self):
        pass

    def _combat_runtime_active(self):
        return False

    def _is_in_dungeon_context(self):
        return True

    def _dungeon_navigate(self, skip_first_screenshot=True):
        return True

    def _update_prepare_resource_snapshot(self, skip_first_screenshot=True):
        stamina = next(self.snapshot_stamina)
        return {"stamina": SimpleNamespace(value=stamina)}

    def _is_fast_combat_locked(self):
        return False

    def _prepare_fast_combat(self, stamina, use_max=False, skip_first_screenshot=True):
        return "ready", 10

    def _run_fast_combat(self, skip_first_screenshot=True):
        return True

    def _prepare_repeat_combat(self, **kwargs):
        self.repeat_prepare_called = True
        return True

    def _leave_to_main(self, skip_first_screenshot=True):
        self.left_to_main = True
        return True

    def _combat_runtime_clear(self):
        pass

    def _combat_delay_after_settled(self):
        self.config.delayed = True

class ManualChecks(unittest.TestCase):
    def test_60_stamina_supports_3_hunt_runs(self):
        self.assertTrue(calculate_fast_combat_target(10, 10, 60, 20) == 3, '60 stamina supports 3 hunt runs')

    def test_daily_remaining_count_is_respected(self):
        self.assertTrue(calculate_fast_combat_target(10, 2, 60, 20) == 2, 'daily remaining count is respected')

    def test_configured_count_is_respected(self):
        self.assertTrue(calculate_fast_combat_target(2, 10, 60, 20) == 2, 'configured count is respected')

    def test_less_than_one_run_returns_zero(self):
        self.assertTrue(calculate_fast_combat_target(10, 10, 19, 20) == 0, 'less than one run returns zero')

    def test_stockpiled_stamina_is_accepted(self):
        self.assertTrue(calculate_fast_combat_target(10, 10, 17940, 20) == 10, 'stockpiled stamina is accepted')

    def test_20_stored_fast_combats_still_cap_one_batch_at_10(self):
        self.assertTrue(calculate_fast_combat_target(10, 20, 400, 20) == 10, '20 stored fast combats still cap one batch at 10')

    def test_daily_repeat_counter_excludes_the_first_actual_run(self):
        # Retain the migration ID; current repeat settings budget Leif units,
        # so they must reserve 80 stamina without subtracting a first run.
        self.assertEqual(repeat_budget(100), (1, (80, 20)))

    def test_daily_repeat_count_is_clamped_by_remaining_stamina(self):
        self.assertEqual(repeat_budget(60), (1, (60, 0)))

    def test_burnout_repeat_counter_excludes_the_first_actual_run(self):
        self.assertEqual(repeat_budget(240, mode="Burnout"), (3, (240, 0)))
        self.assertEqual(repeat_budget(79, mode="Burnout"), (0, (0, 79)))

    def test_240_stamina_prepares_10_fast_combats(self):
        fast_target = calculate_fast_combat_target(10, 10, 240, 20)
        self.assertTrue(fast_target == 10, '240 stamina prepares 10 fast combats')

    def test_fixed_10_run_mode_ends_after_10_fast_combats(self):
        fast_target = calculate_fast_combat_target(10, 10, 240, 20)
        remaining_stamina = 240 - fast_target * 20
        # The retired fixed-run helper is replaced by post-fast-combat resource
        # budgeting: a 40-stamina remainder cannot form a burnout repeat unit.
        self.assertEqual(repeat_budget(remaining_stamina, mode="Burnout"), (0, (0, 40)))

    def test_stamina_thousands_separator_is_normalized(self):
        self.assertTrue(CombatPrepareDigit.__new__(CombatPrepareDigit).after_process("17,940") == "17940", 'stamina thousands separator is normalized')

    def test_saint_3_7_skips_fast_combat(self):
        self.assertTrue(Combat._combat_supports_fast_combat(FastSupportProbe("Saint37")) is False, 'saint 3-7 skips fast combat')

    def test_dimensional_hunt_skips_fast_combat(self):
        self.assertTrue(Combat._combat_supports_fast_combat(FastSupportProbe("Hunt", "Dimensional")) is False, 'dimensional hunt skips fast combat')

    def test_normal_hunt_supports_fast_combat(self):
        self.assertTrue(Combat._combat_supports_fast_combat(FastSupportProbe("Hunt", "Hell")) is True, 'normal hunt supports fast combat')

    def test_configured_fast_count_is_capped_at_10(self):
        self.assertTrue(FastCountProbe()._combat_fast_count() == 10, 'configured fast count is capped at 10')

    def test_prepare_clamps_selected_count(self):
        prepare = FakePrepare(stamina=60)
        status, target = prepare._prepare_fast_combat(stamina=60)
        self.assertTrue(status == "ready" and target == 3 and prepare.target == 3, 'prepare clamps selected count')

    def test_prepare_stops_before_refill_popup(self):
        prepare = FakePrepare(stamina=19)
        status, target = prepare._prepare_fast_combat(stamina=19)
        self.assertTrue(status == "no_stamina" and target == 0, 'prepare stops before refill popup')

    def test_farm_mode_caps_one_fast_combat_launch_at_10(self):
        prepare = FakePrepare(stamina=400, remaining=20)
        status, target = prepare._prepare_fast_combat(stamina=400, use_max=True)
        self.assertTrue(status == "ready" and target == 10 and prepare.target == 10, 'farm mode caps one fast-combat launch at 10')

    def historical_240_stamina_fixed_count_run_finishes_after_fast_combat(self):
        previous = _test_server.lang
        _test_server.set_lang("cn")
        self.addCleanup(_test_server.set_lang, previous)
        run_probe = FixedCountRunProbe()
        self.assertTrue(run_probe.run()
                    and not run_probe.repeat_prepare_called
                    and run_probe.left_to_main
                    and run_probe.config.delayed, '240 stamina fixed-count run finishes after fast combat')

# ruff: noqa: E402
"""Historical manual assertions adapted to the account-free offline runner."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from module.config import server as _test_server
_test_server.set_lang("global_cn")




from datetime import datetime, timedelta



from module.config.config import MultiSetWrapper

from module.config.config_updater import ConfigGenerator, ConfigUpdater, normalize_execution_mode

from module.config.stored.classes import StoredArenaFlag, StoredStamina

from tasks.arena.burnout import ArenaBurnoutMixin

from tasks.dungeon.burnout import (
    ALTAR_STAMINA_COST,
    EPISODE4_STAMINA_COST,
    HUNT_STAMINA_COST,
    SAINT37_STAMINA_COST,
    CombatBurnoutMixin,
)

def now() -> datetime:
    return datetime.now().replace(microsecond=0)

class FakeConfig:
    """Minimal config stand-in for StoredBase binding. Never touches disk."""

    def __init__(self):
        self.data = {}
        self.modified = {}
        self.auto_update = False

    def update(self):
        pass

    def multi_set(self):
        return MultiSetWrapper(main=self)

class FakeStored:
    pass

def make_stamina(value: int, total: int, time: datetime) -> StoredStamina:
    config = FakeConfig()
    config.data = {"DataUpdate": {"Dashboard": {"Stamina": {"time": time, "value": value, "total": total}}}}
    stored = StoredStamina("DataUpdate.Dashboard.Stamina")
    stored._bind(config)
    return stored

def make_flag(value: int, total: int, time: datetime) -> StoredArenaFlag:
    config = FakeConfig()
    config.data = {"DataUpdate": {"Dashboard": {"ArenaFlag": {"time": time, "value": value, "total": total}}}}
    stored = StoredArenaFlag("DataUpdate.Dashboard.ArenaFlag")
    stored._bind(config)
    return stored

def near(a: datetime, b: datetime, seconds: int = 3) -> bool:
    return abs((a - b).total_seconds()) <= seconds

class DelayRecorder:
    def __init__(self, stamina=None, flag=None):
        self.stored = FakeStored()
        self.stored.Stamina = stamina
        self.stored.ArenaFlag = flag
        self.delay_calls = []

    def task_delay(self, success=None, server_update=None, target=None, minute=None, task=None):
        self.delay_calls.append(
            {"success": success, "server_update": server_update, "target": target, "minute": minute}
        )

class FakeCombat(CombatBurnoutMixin):
    def __init__(self, config, domain="Hunt", grade="Hell", use_fast=True,
                 fast_count=10, repeat_count=5, burnout=True, farm=False):
        self.config = config
        self._domain = domain
        self._grade = grade
        self._use_fast = use_fast
        self._fast_count = fast_count
        self._repeat_count = repeat_count
        self._burnout = burnout
        self._farm = farm

    def _combat_is_farm_task(self):
        return self._farm

    def _uses_server_repeat_combat(self):
        # These historical checks cover the legacy, stamina-based selector.
        return False

    def _combat_burnout_enabled(self):
        if self._farm:
            return False
        return self._burnout

    def _dungeon_domain(self):
        return self._domain

    def _combat_grade(self):
        return self._grade

    def _combat_should_use_fast(self):
        return self._use_fast

    def _combat_fast_count(self):
        return self._fast_count

    def _combat_repeat_count(self):
        return self._repeat_count

    def _combat_burnout_refresh_status(self):
        # Offline: skip resource-bar OCR, rely on injected stored values.
        return False

class FakeArenaConfig(DelayRecorder):
    def __init__(self, flag, npc_combat=True, burnout=True):
        super().__init__(flag=flag)
        self.Arena_BurnoutMode = burnout
        self.Arena_NPCCombat = npc_combat

class FakeArena(ArenaBurnoutMixin):
    def __init__(self, config):
        self.config = config

    def _update_arena_dashboard_snapshot(self, skip_first_screenshot=True):
        # Offline: skip resource-bar OCR, rely on injected stored values.
        return False

from datetime import datetime as _real_datetime
from unittest.mock import patch

class FrozenDateTime(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 2, 12, 0, 0, tzinfo=tz)

datetime = FrozenDateTime

class ManualChecks(unittest.TestCase):
    def setUp(self):
        clock = patch('module.config.stored.classes.now', return_value=datetime.now())
        clock.start()
        self.addCleanup(clock.stop)

    def test_recover_10_in_40min(self):
        s = make_stamina(100, 336, now() - timedelta(minutes=40))
        self.assertTrue(s.predict_current() == 110, f"got {s.predict_current()}")

    def test_recovery_capped_at_total(self):
        s = make_stamina(334, 336, now() - timedelta(hours=5))
        self.assertTrue(s.predict_current() == 336, f"got {s.predict_current()}")

    def test_overflow_never_regenerates(self):
        s = make_stamina(400, 336, now() - timedelta(hours=5))
        self.assertTrue(s.predict_current() == 400, f"got {s.predict_current()}")

    def test_future_record_returns_raw_value(self):
        s = make_stamina(100, 336, now() + timedelta(hours=1))
        self.assertTrue(s.predict_current() == 100, f"got {s.predict_current()}")

    def test_unknown_total_returns_raw_value(self):
        s = make_stamina(100, 0, now() - timedelta(hours=1))
        self.assertTrue(s.predict_current() == 100, f"got {s.predict_current()}")

    def test_deficit_100_400min(self):
        t0 = now()
        s = make_stamina(100, 336, t0)
        expect = t0 + timedelta(minutes=(200 - 100) * 4)
        got = s.predict_reach_time(200)
        self.assertTrue(near(got, expect), f"got {got}, expect {expect}")

    def test_partial_tick_credited(self):
        t0 = now()
        s = make_stamina(100, 336, t0 - timedelta(minutes=2))
        expect = t0 + timedelta(minutes=400 - 2)
        got = s.predict_reach_time(200)
        self.assertTrue(near(got, expect), f"got {got}, expect {expect}")

    def test_already_satisfied_now(self):
        t0 = now()
        s = make_stamina(250, 336, t0)
        got = s.predict_reach_time(200)
        self.assertTrue(near(got, t0), f"got {got}")

    def test_flags_recover_hourly(self):
        t0 = now()
        f = make_flag(2, 5, t0 - timedelta(minutes=30))
        self.assertTrue(f.predict_current() == 2, f"got {f.predict_current()}")

    def test_flags_full_in_2_5h(self):
        t0 = now()
        f = make_flag(2, 5, t0 - timedelta(minutes=30))
        expect = t0 + timedelta(hours=3, minutes=-30)
        got = f.predict_reach_time(5)
        self.assertTrue(near(got, expect), f"got {got}, expect {expect}")

    def test_event_flags_overflow_untouched(self):
        t0 = now()
        f = make_flag(344, 5, t0 - timedelta(hours=9))
        self.assertTrue(f.predict_current() == 344, f"got {f.predict_current()}")

    def test_episode4_cost(self):
        self.assertTrue(EPISODE4_STAMINA_COST == 20, 'episode4 cost')

    def test_saint37_cost(self):
        self.assertTrue(SAINT37_STAMINA_COST == 8, 'saint37 cost')

    def test_hunt_costs(self):
        self.assertTrue(HUNT_STAMINA_COST == {"Mid": 16, "High": 18, "Hell": 20}, 'hunt costs')

    def test_altar_costs(self):
        self.assertTrue(ALTAR_STAMINA_COST == {"Pri": 9, "Mid": 10, "High": 11, "Hell": 12}, 'altar costs')

    def test_dimensional_excluded(self):
        self.assertTrue("Dimensional" not in HUNT_STAMINA_COST, 'dimensional excluded')

    def test_altar_pri_wakes_for_one_stage(self):
        combat = FakeCombat(DelayRecorder(), domain="SpiritAltar", grade="Pri", use_fast=False, repeat_count=5)
        self.assertTrue(combat._combat_burnout_batch_need() == 9, f"got {combat._combat_burnout_batch_need()}")

    def test_hunt_hell_wakes_for_one_stage(self):
        combat = FakeCombat(DelayRecorder(), domain="Hunt", grade="Hell", use_fast=True, fast_count=10)
        self.assertTrue(combat._combat_burnout_batch_need() == 20, f"got {combat._combat_burnout_batch_need()}")

    def test_dimensional_batch_none(self):
        combat = FakeCombat(DelayRecorder(), domain="Hunt", grade="Dimensional")
        self.assertTrue(combat._combat_burnout_batch_need() is None, 'dimensional batch None')

    def test_dimensional_not_scheduled(self):
        t0 = now()
        config = DelayRecorder(stamina=make_stamina(300, 336, t0))
        combat = FakeCombat(config, domain="Hunt", grade="Dimensional")
        self.assertTrue(combat._combat_burnout_schedule() is False and not config.delay_calls, 'dimensional -> not scheduled')

    def test_unknown_stamina_not_scheduled(self):
        t0 = now()
        config = DelayRecorder(stamina=make_stamina(0, 0, t0))
        combat = FakeCombat(config)
        self.assertTrue(combat._combat_burnout_schedule() is False and not config.delay_calls, 'unknown stamina -> not scheduled')

    def test_combat_disabled_not_scheduled(self):
        t0 = now()
        config = DelayRecorder(stamina=make_stamina(300, 336, t0))
        combat = FakeCombat(config, burnout=False)
        self.assertTrue(combat._combat_burnout_schedule() is False and not config.delay_calls, 'disabled -> not scheduled')

    def test_farm_task_not_scheduled(self):
        t0 = now()
        config = DelayRecorder(stamina=make_stamina(300, 336, t0))
        combat = FakeCombat(config, farm=True)
        self.assertTrue(combat._combat_burnout_schedule() is False and not config.delay_calls, 'farm task -> not scheduled')

    def test_sufficient_minute_recheck(self):
        t0 = now()
        config = DelayRecorder(stamina=make_stamina(300, 336, t0))
        combat = FakeCombat(config, domain="Hunt", grade="Hell", use_fast=True, fast_count=10)
        self.assertTrue(combat._combat_burnout_schedule() is True
                    and config.delay_calls
                    and config.delay_calls[-1]["minute"] == combat.COMBAT_BURNOUT_RECHECK_MINUTES, f"calls={config.delay_calls}")

    def test_deficit_target_time(self):
        t0 = now()
        config = DelayRecorder(stamina=make_stamina(0, 336, t0))
        combat = FakeCombat(config, domain="Hunt", grade="Hell", use_fast=False, repeat_count=5)
        result = combat._combat_burnout_schedule()
        expect = t0 + timedelta(minutes=20 * 4)
        got = config.delay_calls[-1]["target"] if config.delay_calls else None
        self.assertTrue(result is True and got is not None and near(got, expect), f"got {got}, expect {expect}")

    def test_fixed_repeat_count_does_not_affect_wake_up(self):
        combat = FakeCombat(DelayRecorder(), domain="Hunt", grade="Hell", use_fast=False, repeat_count=30)
        self.assertTrue(combat._combat_burnout_batch_need() == 20, 'fixed repeat count does not affect wake-up')

    def test_worst_wait_24h_clamp(self):
        t0 = now()
        worst = make_stamina(0, 336, t0)
        got = worst.predict_reach_time(336)
        self.assertTrue(got - t0 < timedelta(hours=24), f"got {got - t0}")

    def test_empty_flags_full_in_5h(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(0, 5, t0))
        arena = FakeArena(config)
        result = arena._arena_burnout_schedule()
        expect = t0 + timedelta(hours=5)
        got = config.delay_calls[-1]["target"] if config.delay_calls else None
        self.assertTrue(result is True and got is not None and near(got, expect), f"got {got}, expect {expect}")

    def test_full_flags_minute_recheck(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(5, 5, t0))
        arena = FakeArena(config)
        self.assertTrue(arena._arena_burnout_schedule() is True
                    and config.delay_calls
                    and config.delay_calls[-1]["minute"] == arena.ARENA_BURNOUT_RECHECK_MINUTES, f"calls={config.delay_calls}")

    def test_overflow_flags_minute_recheck(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(344, 5, t0))
        arena = FakeArena(config)
        self.assertTrue(arena._arena_burnout_schedule() is True and config.delay_calls[-1]["minute"] is not None, 'overflow flags -> minute recheck')

    def test_npc_combat_off_not_scheduled(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(3, 5, t0), npc_combat=False)
        arena = FakeArena(config)
        self.assertTrue(arena._arena_burnout_schedule() is False and not config.delay_calls, 'npc combat off -> not scheduled')

    def test_unknown_flags_not_scheduled(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(0, 0, t0))
        arena = FakeArena(config)
        self.assertTrue(arena._arena_burnout_schedule() is False and not config.delay_calls, 'unknown flags -> not scheduled')

    def test_arena_disabled_not_scheduled(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(3, 5, t0), burnout=False)
        arena = FakeArena(config)
        self.assertTrue(arena._arena_burnout_schedule() is False and not config.delay_calls, 'disabled -> not scheduled')

    def test_fallback_to_server_update(self):
        t0 = now()
        config = FakeArenaConfig(make_flag(3, 5, t0), burnout=False)
        arena = FakeArena(config)
        arena._arena_delay_after_run()
        self.assertTrue(config.delay_calls and config.delay_calls[-1]["server_update"] is True, f"calls={config.delay_calls}")

    def test_legacy_true_burnout(self):
        self.assertTrue(normalize_execution_mode(True) == "Burnout", 'legacy true -> burnout')

    def test_legacy_false_daily(self):
        self.assertTrue(normalize_execution_mode(False) == "Daily", 'legacy false -> daily')

    def test_arena_burnout_hides_fixed_count(self):
        updater = ConfigUpdater()
        arena_burnout = {
                    "Arena": {"Arena": {"NPCCombat": True, "BurnoutMode": "Burnout"}},
                }
        self.assertTrue("Arena.Arena.NPCCombatCount" in updater.get_hidden_args(arena_burnout), 'arena burnout hides fixed count')

    def test_arena_daily_shows_fixed_count(self):
        updater = ConfigUpdater()
        arena_daily = {
                    "Arena": {"Arena": {"NPCCombat": True, "BurnoutMode": "Daily"}},
                }
        self.assertTrue("Arena.Arena.NPCCombatCount" not in updater.get_hidden_args(arena_daily), 'arena daily shows fixed count')

    def test_combat_burnout_removes_repeat_count(self):
        updater = ConfigUpdater()
        combat_burnout = {
                    "Combat": {
                        "Combat": {
                            "Domain": "Hunt",
                            "HuntGrade": "Hell",
                            "FastCombat": True,
                            "BurnoutMode": "Burnout",
                        }
                    }
                }
        hidden = updater.get_hidden_args(combat_burnout)
        self.assertNotIn("RepeatCombatCount", updater.args["Combat"]["Combat"])
        self.assertNotIn("Combat.Combat.RepeatCombatCount", hidden)
        self.assertIn("Combat.Combat.RepeatCombatLeifCount", hidden)

    def historical_combat_burnout_keeps_fast_count(self):
        updater = ConfigUpdater()
        combat_burnout = {
                    "Combat": {
                        "Combat": {
                            "Domain": "Hunt",
                            "HuntGrade": "Hell",
                            "FastCombat": True,
                            "BurnoutMode": "Burnout",
                        }
                    }
                }
        hidden = updater.get_hidden_args(combat_burnout)
        self.assertTrue("Combat.Combat.FastCombatCount" not in hidden, 'combat burnout keeps fast count')


class EpisodeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Build definitions in memory so these checks never generate files or
        # read an account configuration while testing the configuration source.
        cls.args = ConfigGenerator().args

    def updater(self):
        updater = ConfigUpdater()
        updater.args = self.args
        return updater

    def test_defaults_keep_legacy_material_selection(self):
        from module.config.config_generated import GeneratedConfig

        definitions = self.args["Combat"]["Combat"]
        generated = ConfigUpdater().args["Combat"]["Combat"]
        for field in ("Domain", "EpisodeNumber", "Episode4Material"):
            self.assertEqual(generated[field], definitions[field])
        self.assertEqual(GeneratedConfig.Combat_EpisodeNumber, 4)
        root = Path(__file__).resolve().parents[2]
        template = json.loads((root / "config/template.json").read_text(encoding="utf-8"))
        self.assertEqual(template["Combat"]["Combat"]["EpisodeNumber"], 4)
        self.assertEqual(template["Combat"]["Combat"]["Episode4Material"], "BREATH_OF_KARMA")
        self.assertIn("Episode4", definitions["Domain"]["option"])
        self.assertEqual(definitions["EpisodeNumber"]["value"], 4)
        self.assertEqual(definitions["EpisodeNumber"]["option"], [1, 2, 3, 4, 5])
        self.assertEqual(definitions["Episode4Material"]["value"], "BREATH_OF_KARMA")
        self.assertEqual(
            definitions["Episode4Material"]["option"],
            [
                "CATALYST_RARE_BENEVOLENT", "CATALYST_RARE_SECRET",
                "CATALYST_RARE_FIGHTING_SPIRIT", "CATALYST_RARE_SNIPER",
                "HEART_OF_THE_WOODS", "CATALYST_RARE_OATH",
                "CATALYST_RARE_MYSTERIOUS", "CATALYST_EPIC_OATH",
                "BREATH_OF_KARMA", "CATALYST_EPIC_SNIPER",
                "CATALYST_EPIC_FIGHTING_SPIRIT", "FROZEN_SEED",
                "CATALYST_EPIC_BENEVOLENT", "CATALYST_EPIC_MYSTERIOUS",
                "CATALYST_EPIC_SECRET", "TRACES_OF_BRILLIANCE",
            ],
        )

    def test_old_configs_default_to_four_without_replacing_material(self):
        updater = self.updater()
        for material in self.args["Combat"]["Combat"]["Episode4Material"]["option"]:
            with self.subTest(material=material):
                old = {"Combat": {"Combat": {"Domain": "Episode4", "Episode4Material": material}}}
                snapshot = deepcopy(old)
                updated = updater.config_update(old)["Combat"]["Combat"]
                self.assertEqual(updated["Domain"], "Episode4")
                self.assertEqual(updated["EpisodeNumber"], 4)
                self.assertEqual(updated["Episode4Material"], material)
                self.assertEqual(old, snapshot)

    def test_explicit_episode_and_material_survive_config_update(self):
        updater = self.updater()
        for episode in range(1, 6):
            with self.subTest(episode=episode):
                old = {"Combat": {"Combat": {
                    "Domain": "Episode4", "EpisodeNumber": episode,
                    "Episode4Material": "BREATH_OF_KARMA",
                }}}
                updated = updater.config_update(old)["Combat"]["Combat"]
                self.assertEqual(updated["EpisodeNumber"], episode)
                # Unsupported combinations must reach business validation; the
                # config updater must not silently choose a different material.
                self.assertEqual(updated["Episode4Material"], "BREATH_OF_KARMA")

    def test_episode_controls_follow_domain_without_changing_saved_values(self):
        updater = self.updater()
        paths = {"Combat.Combat.EpisodeNumber", "Combat.Combat.Episode4Material"}
        self.assertTrue(paths.issubset(updater.get_hidden_args({})))
        data = {"Combat": {"Combat": {
            "EpisodeNumber": 2, "Episode4Material": "CATALYST_RARE_SECRET",
        }}}
        for domain in ("Episode4", "Hunt", "Episode4", "SpiritAltar", "Saint37", "Episode4"):
            with self.subTest(domain=domain):
                data["Combat"]["Combat"]["Domain"] = domain
                for fast_combat in (True, False):
                    data["Combat"]["Combat"]["FastCombat"] = fast_combat
                    snapshot = deepcopy(data)
                    hidden = updater.get_hidden_args(data)
                    self.assertEqual(paths & hidden, set() if domain == "Episode4" else paths)
                    self.assertEqual(data, snapshot)
                    updated = updater.config_update(data)["Combat"]["Combat"]
                    self.assertEqual(updated["EpisodeNumber"], 2)
                    self.assertEqual(updated["Episode4Material"], "CATALYST_RARE_SECRET")

    def test_translations_cover_episode_choices_and_legacy_materials(self):
        root = Path(__file__).resolve().parents[2] / "module" / "config" / "i18n"
        material_options = self.args["Combat"]["Combat"]["Episode4Material"]["option"]
        for language in ("zh-CN", "zh-TW", "en-US", "ja-JP", "es-ES"):
            with self.subTest(language=language):
                combat = json.loads((root / f"{language}.json").read_text(encoding="utf-8"))["Combat"]
                fields = {
                    "Domain": ("help", "Episode4"),
                    "EpisodeNumber": ("name", "help", "1", "2", "3", "4", "5"),
                    "Episode4Material": ("name", "help", *material_options),
                }
                for field, keys in fields.items():
                    for key in keys:
                        value = combat[field][key]
                        self.assertTrue(value.strip(), f"{language}: Combat.{field}.{key}")
                        self.assertFalse(value.startswith("Combat."), value)

    def test_material_options_follow_episode_and_put_growth_materials_last(self):
        updater = self.updater()
        path = "Combat.Combat.Episode4Material"
        rare = [
            "CATALYST_RARE_BENEVOLENT", "CATALYST_RARE_SECRET",
            "CATALYST_RARE_FIGHTING_SPIRIT", "CATALYST_RARE_SNIPER",
            "CATALYST_RARE_OATH", "CATALYST_RARE_MYSTERIOUS",
        ]
        catalysts = rare + [
            "CATALYST_EPIC_OATH", "CATALYST_EPIC_SNIPER",
            "CATALYST_EPIC_FIGHTING_SPIRIT", "CATALYST_EPIC_BENEVOLENT",
            "CATALYST_EPIC_MYSTERIOUS", "CATALYST_EPIC_SECRET",
        ]
        growth = ["HEART_OF_THE_WOODS", "BREATH_OF_KARMA", "FROZEN_SEED", "TRACES_OF_BRILLIANCE"]
        expected = {1: rare, 2: catalysts, 3: catalysts, 4: catalysts + growth, 5: catalysts}
        for episode, options in expected.items():
            with self.subTest(episode=episode):
                data = {"Combat": {"Combat": {"EpisodeNumber": episode}}}
                self.assertEqual(updater.get_select_options(data), {path: options})
        self.assertEqual(updater.get_select_options({}), {path: catalysts + growth})

    def test_invalid_episodes_offer_no_materials(self):
        updater = self.updater()
        for episode in (0, 6, -1, "4", None, True, 4.0, [], {}):
            with self.subTest(episode=episode):
                data = {"Combat": {"Combat": {"EpisodeNumber": episode}}}
                self.assertEqual(updater.get_select_options(data), {"Combat.Combat.Episode4Material": []})

    def test_filtered_options_preserve_saved_values_and_shared_definitions(self):
        updater = self.updater()
        original_args = deepcopy(updater.args)
        data = {"Combat": {"Combat": {
            "Domain": "Episode4", "EpisodeNumber": 4, "Episode4Material": "BREATH_OF_KARMA",
        }}}
        for episode in (4, 1, 2, 5, 4):
            with self.subTest(episode=episode):
                data["Combat"]["Combat"]["EpisodeNumber"] = episode
                original_data = deepcopy(data)
                selected = updater.get_select_options(data)
                self.assertEqual(data, original_data)
                self.assertEqual(updater.args, original_args)
                self.assertEqual(updater.config_update(data)["Combat"]["Combat"]["Episode4Material"],
                                 "BREATH_OF_KARMA")
                selected["Combat.Combat.Episode4Material"].clear()
                self.assertTrue(updater.get_select_options(data)["Combat.Combat.Episode4Material"])
                self.assertEqual(updater.args, original_args)

    def test_unavailable_selection_hint_is_defined_in_all_languages(self):
        generator = ConfigGenerator()
        self.assertIn("SelectUnavailable", generator.gui["Text"])
        root = Path(__file__).resolve().parents[2] / "module" / "config" / "i18n"
        for language in ("zh-CN", "zh-TW", "en-US", "ja-JP", "es-ES"):
            with self.subTest(language=language):
                data = json.loads((root / f"{language}.json").read_text(encoding="utf-8"))
                value = data["Gui"]["Text"]["SelectUnavailable"]
                self.assertTrue(value.strip())
                self.assertFalse(value.startswith("Gui."), value)

"""Profile boundaries, team rules, file isolation and partial observations."""

import json
import unittest
from dataclasses import replace
from uuid import uuid4

import yaml

from tasks.automaton_tower.device_record import DeviceRecord
from tasks.automaton_tower.policy import (
    Candidate,
    DecisionUnavailable,
    DeviceCatalog,
    ProfileError,
    choose_device,
    parse_profile,
)
from tasks.automaton_tower.profile_store import ProfileStore
from tests.support.offline import ROOT


def workspace():
    # Keep test-generated files in the ignored workspace, without deleting
    # user files or relying on real account configuration.
    path = ROOT / ".cache/automaton_tower_tests" / uuid4().hex
    path.mkdir(parents=True)
    return path


def profile_text(**values):
    return yaml.safe_dump(
        {"版本": 1, "方案名": "测试方案", **values}, allow_unicode=True, sort_keys=False
    )


class TowerProfileTests(unittest.TestCase):
    def setUp(self):
        self.catalog = DeviceCatalog()

    def offers(self, *names):
        return tuple(
            Candidate(slot, self.catalog.require(name))
            for slot, name in enumerate(names)
        )

    def test_priority_beats_card_position_and_same_rule_uses_leftmost(self):
        offers = self.offers(
            "暴击率强化装置", "单一攻击2级强化装置", "后排英雄强化装置"
        )
        profile = parse_profile(
            profile_text(装置优先顺序=["单一攻击2级强化装置", "暴击率强化装置"])
        )
        self.assertEqual(choose_device(profile, offers).candidate.slot, 1)
        same_rank = parse_profile(
            profile_text(
                装置优先顺序=[{"装置": ["单一攻击2级强化装置", "暴击率强化装置"]}]
            )
        )
        self.assertEqual(choose_device(same_rank, offers).candidate.slot, 0)

    def test_conditions_use_declared_main_and_do_not_infer_other_members(self):
        offers = self.offers(
            "（战士）攻击强化装置", "（盗贼）基本技能连射装置", "暴击率强化装置"
        )
        text = profile_text(
            队伍={"主输出职业": "盗贼"},
            装置优先顺序=[
                {"装置": "（战士）攻击强化装置", "条件": {"主输出职业": "战士"}},
                {"装置": "（盗贼）基本技能连射装置", "条件": {"主输出职业": "盗贼"}},
            ],
        )
        profile = parse_profile(text)
        self.assertEqual(choose_device(profile, offers).candidate.slot, 1)
        self.assertIsNone(
            profile.team.applicable(self.catalog.require("（战士）攻击强化装置"))
        )

    def test_missing_condition_blocks_only_when_it_could_change_choice(self):
        offers = self.offers(
            "暴击率强化装置", "单一攻击2级强化装置", "后排英雄强化装置"
        )
        rule = {"装置": "单一攻击2级强化装置", "条件": {"主输出职业": "盗贼"}}
        with self.assertRaises(DecisionUnavailable):
            choose_device(
                parse_profile(profile_text(装置优先顺序=[rule, "暴击率强化装置"])),
                offers,
            )
        profile = parse_profile(profile_text(装置优先顺序=["暴击率强化装置", rule]))
        self.assertEqual(choose_device(profile, offers).candidate.slot, 0)

    def test_complete_team_excludes_absent_profession_and_requires_same_hero(self):
        heroes = [
            {"姓名": "甲", "职业": "盗贼", "属性": "火焰", "位置": "前排"},
            {"姓名": "乙", "职业": "精灵师", "属性": "寒气", "位置": "后排"},
            {"姓名": "丙", "职业": "射手", "属性": "自然", "位置": "上方"},
            {"姓名": "丁", "职业": "魔导士", "属性": "光明", "位置": "下方"},
        ]
        profile = parse_profile(profile_text(队伍={"英雄": heroes, "主输出": "甲"}))
        offers = self.offers(
            "（战士）攻击强化装置",
            "未知开拓者佛里蒂丝强化装置",
            "（盗贼）基本技能连射装置",
        )
        self.assertEqual(choose_device(profile, offers).candidate.slot, 2)
        self.assertFalse(profile.team.condition("队伍包含英雄", "未知开拓者佛里蒂丝"))
        self.assertTrue(profile.team.condition("队伍包含英雄", "甲"))
        combined = replace(
            self.catalog.require("（盗贼）基本技能连射装置"), position="后排"
        )
        self.assertFalse(profile.team.applicable(combined))
        position_only = parse_profile(
            profile_text(队伍={"英雄": heroes, "主输出位置": "前排"})
        )
        self.assertFalse(
            position_only.team.applicable(self.catalog.require("（战士）攻击强化装置"))
        )
        with self.assertRaises(ProfileError):
            parse_profile(profile_text(队伍={"英雄": heroes, "主输出职业": "骑士"}))

    def test_deny_list_survives_fallback_and_all_denied_stops(self):
        offers = self.offers(
            "暴击率强化装置", "单一攻击2级强化装置", "属性相克强化装置"
        )
        profile = parse_profile(profile_text(禁选装置=["暴击率强化装置"]))
        self.assertEqual(choose_device(profile, offers).candidate.slot, 1)
        with self.assertRaises(DecisionUnavailable):
            choose_device(
                parse_profile(profile_text(禁选装置=[c.device.name for c in offers])),
                offers,
            )
        with self.assertRaises(DecisionUnavailable):
            choose_device(parse_profile(profile_text(名单外处理="暂停")), offers)
        with self.assertRaises(DecisionUnavailable):
            choose_device(profile, offers[:2])

    def test_partial_team_does_not_turn_unknown_applicability_into_absence(self):
        offers = self.offers(
            "（自然）暴击强化装置", "暴击率强化装置", "单一攻击2级强化装置"
        )
        with self.assertRaises(DecisionUnavailable):
            choose_device(parse_profile(profile_text()), offers)
        profile = parse_profile(profile_text(队伍={"主输出属性": "自然"}))
        self.assertEqual(choose_device(profile, offers).candidate.slot, 0)

    def test_invalid_profiles_reject_unknown_keys_names_and_ambiguous_team(self):
        bad = [
            {"版本": True},
            {"版本": 2},
            {"未知键": 1},
            {"方案名": ""},
            {"装置优先顺序": ["暴击率"]},
            {"装置优先顺序": ["暴击率强化装置", "暴击率强化装置"]},
            {
                "装置优先顺序": [
                    {"装置": "暴击率强化装置", "条件": {"已获得": "某装置"}}
                ]
            },
            {"队伍": {"英雄": [{"姓名": "甲"}, {"姓名": "甲"}]}},
            {
                "队伍": {
                    "英雄": [
                        {"姓名": "甲", "位置": "后排"},
                        {"姓名": "乙", "位置": "后排"},
                    ]
                }
            },
            {"队伍": {"主输出": "不在名单中"}},
            {"队伍": {"主输出职业": []}},
            {
                "队伍": {
                    "英雄": [{"姓名": "甲", "职业": "战士"}],
                    "主输出": "甲",
                    "主输出职业": "盗贼",
                }
            },
            {"名单外处理": None},
            {"识别失败处理": "随便选"},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ProfileError):
                parse_profile(profile_text(**value))
        for content in (
            "方案名: 甲\n方案名: 乙\n",
            "!!python/object/apply:os.system ['invalid']",
            b"\xff",
            "a" * 131073,
        ):
            with (
                self.subTest(content=str(content)[:60]),
                self.assertRaises(ProfileError),
            ):
                parse_profile(content)

    def test_full_name_matching_keeps_attributes_and_level_digits(self):
        for name in (
            "（自然）暴击强化装置",
            "（火焰）暴击强化装置",
            "（光明/黑暗）暴击强化装置",
        ):
            self.assertIsNotNone(
                self.catalog.resolve(name.replace("（", "(").replace("）", ")"))
            )
        for name in ("未知开拓者佛里蒂丝强化装", "暴击强化装置", "单一攻击1级强化装置"):
            self.assertIsNone(self.catalog.resolve(name))

    def test_profile_import_export_and_accounts_are_separate_without_overwrite(self):
        root = workspace()
        store = ProfileStore(root)
        self.assertIsNone(store.active("甲账号"))
        self.assertFalse(store.instances.exists())
        content = profile_text(装置优先顺序=["暴击率强化装置"])
        first, second = store.import_profile(content), store.import_profile(content)
        self.assertNotEqual(first, second)
        store.activate("甲账号", first)
        store.activate("乙账号", second)
        self.assertEqual(store.export(first).decode("utf-8"), content)
        self.assertEqual(store.active_id("甲账号"), first)
        self.assertEqual(store.active_id("乙账号"), second)
        store.activate("甲账号", None)
        self.assertIsNone(store.active("甲账号"))
        self.assertEqual(len(list(store.user.glob("*.yaml"))), 2)
        self.assertEqual(len(store.list_profiles()), 2)
        for bad in ("user:../escape", "builtin:CON", "user:甲/乙", "bad:abc"):
            with self.subTest(bad=bad), self.assertRaises(ProfileError):
                store.export(bad)
        with self.assertRaises(ProfileError):
            store.activate("../account", first)

    def test_invalid_active_profile_never_silently_becomes_legacy_mode(self):
        store = ProfileStore(workspace())
        selected = store.import_profile(profile_text())
        store.activate("offline", selected)
        (store.user / (selected.split(":")[1] + ".yaml")).write_text(
            "方案名: 损坏\n未知字段: true\n", encoding="utf-8"
        )
        with self.assertRaises(ProfileError):
            store.active("offline")
        self.assertEqual(store.active_id("offline"), selected)
        self.assertIsNotNone(store.list_profiles()[0].error)
        store.activate("offline", None)
        self.assertIsNone(store.active("offline"))

    def test_debug_setting_is_opt_in_per_account_and_independent_of_profiles(self):
        store = ProfileStore(workspace())
        self.assertFalse(store.debug_enabled("offline"))
        self.assertFalse(store.debug_settings.exists())
        profile = store.import_profile(profile_text())
        store.set_debug_enabled("offline", True)
        store.activate("offline", profile)
        store.activate("offline", None)
        self.assertTrue(store.debug_enabled("offline"))
        self.assertFalse(store.debug_enabled("another"))
        self.assertNotIn("enabled", store.export(profile).decode("utf-8"))
        for invalid in (1, "true", None):
            with self.subTest(value=invalid), self.assertRaises(ProfileError):
                store.set_debug_enabled("offline", invalid)
        settings = store.debug_settings / "offline.json"
        settings.write_text('{"enabled": "true"}', encoding="utf-8")
        with self.assertRaises(ProfileError):
            store.debug_enabled("offline")
        store.set_debug_enabled("offline", False)
        self.assertFalse(store.debug_enabled("offline"))

    def test_journal_requires_confirmation_and_never_claims_complete_inventory(self):
        root = workspace()
        record = DeviceRecord("offline", "global_cn", "测试方案", root)
        offers = self.offers(
            "暴击率强化装置", "单一攻击2级强化装置", "属性相克强化装置"
        )
        choice = choose_device(parse_profile(profile_text()), offers)
        record.begin(offers, choice, 10)
        self.assertFalse(record.confirmed("prepare"))
        record.requested()
        self.assertEqual(record.data["choices"][0]["status"], "confirmation_requested")
        self.assertTrue(record.confirmed("prepare"))
        self.assertFalse(record.confirmed("prepare"))
        saved = json.loads(record.path.read_text(encoding="utf-8"))
        self.assertFalse(saved["inventory_complete"])
        self.assertIsNone(saved["period"])
        self.assertIsNone(saved["difficulty"])
        self.assertEqual(len(saved["choices"]), 1)
        self.assertEqual(saved["choices"][0]["floor"], 10)
        restarted = DeviceRecord("offline", "global_cn", "测试方案", root)
        self.assertNotEqual(restarted.path, record.path)
        self.assertEqual(restarted.data["choices"], [])

    def test_builtin_profile_is_valid_and_export_has_no_runtime_observations(self):
        store = ProfileStore()
        content = store.export("builtin:thief_back")
        profile = parse_profile(content)
        self.assertEqual(profile.name, "后排盗贼输出示例")
        data = yaml.safe_load(content)
        self.assertNotIn("choices", data)
        self.assertNotIn("account", data)
        self.assertNotIn("inventory_complete", data)

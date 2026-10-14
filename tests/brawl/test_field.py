import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from module.config import server

server.set_lang("global_cn")

from tasks.brawl.assets.assets_brawl_field import FIELD_GLEN  # noqa: E402
from tasks.brawl.field import SUPPORTED_FIELD_HEROES, read_field  # noqa: E402
from tasks.brawl.traits import read_field_traits  # noqa: E402
from tasks.brawl.vision import BrawlVision  # noqa: E402
from tests.support.brawl import frame  # noqa: E402


class FieldRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_independent_captures_locate_three_supported_heroes(self):
        # Every identity is checked against a capture other than its asset's
        # source. These are real idle poses, not pasted sprite positives.
        for sample, name, foot in (("field-five-next", "格伦", (731, 409)),
                                   ("queue-empty", "辅助型拉兹", (794, 353)),
                                   ("field-four", "卡琳", (794, 465))):
            with self.subTest(sample=sample, hero=name):
                self.frame = frame(sample)
                observed = read_field(self.frame)
                matches = [hero for hero in observed.heroes if hero.name == name]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0].foot, foot)
                self.assertIsNone(matches[0].imprint)
                self.assertEqual(observed.unknown_count,
                                 observed.panel.deployed - len(observed.heroes))
                self.assertFalse(observed.complete)

    def test_partial_formation_never_uses_bench_names_or_guesses_other_sprites(self):
        self.frame = frame("field-five-bench")
        observed = read_field(self.frame)
        self.assertEqual([hero.name for hero in observed.heroes], ["辅助型拉兹"])
        self.assertEqual(observed.unknown_count, 4)
        self.assertFalse(observed.identity_complete)
        self.assertFalse(observed.complete)
        self.assertEqual(SUPPORTED_FIELD_HEROES, ("格伦", "辅助型拉兹", "卡琳", "战斗型玛雅", "小春"))

    def test_source_imprint_is_not_carried_into_current_hero(self):
        for sample in ("field-glen", "field-five"):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observed = read_field(self.frame)
                self.assertTrue(observed.heroes)
                self.assertTrue(all(hero.imprint is None for hero in observed.heroes))
                self.assertFalse(observed.complete)

    def test_unmodeled_hero_and_deployment_animation_stay_unread(self):
        for sample in ("normal-deployed", "formation-deploy-animation"):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observed = read_field(self.frame)
                self.assertEqual(observed.heroes, ())
                self.assertFalse(observed.identity_complete)
                self.assertFalse(observed.complete)

    def test_empty_field_does_not_reuse_a_prior_identity(self):
        self.frame = frame("field-glen")
        prior = read_field(self.frame)
        self.assertEqual([hero.name for hero in prior.heroes], ["格伦"])
        self.assertTrue(prior.identity_complete)
        self.frame = frame("queue-top")
        current = read_field(self.frame)
        self.assertEqual(current.heroes, ())
        self.assertEqual(current.unknown_count, 0)
        self.assertTrue(current.identity_complete)
        self.assertTrue(current.complete)

    def test_obscured_identity_patch_is_not_completed_from_the_prior_frame(self):
        self.frame = frame("field-glen")
        self.assertTrue(read_field(self.frame).heroes)
        self.frame[287:350, 714:752] = 0
        observed = read_field(self.frame)
        self.assertEqual(observed.heroes, ())
        self.assertEqual(observed.unknown_count, 1)
        self.assertFalse(observed.identity_complete)

    def test_duplicate_sprite_positions_are_ambiguous(self):
        self.frame = frame("field-glen")
        self.frame[287:350, 465:503] = self.frame[287:350, 714:752]
        observed = read_field(self.frame)
        self.assertEqual(observed.heroes, ())
        self.assertEqual(observed.unknown_count, 1)
        self.assertFalse(observed.identity_complete)
        self.assertIn("multiple", observed.reason)

    def test_mismatched_deployed_count_does_not_publish_a_complete_field(self):
        self.frame = frame("field-glen")
        current = BrawlVision(self.frame).observe()
        contradictory = replace(current, panel=replace(current.panel, deployed=0))
        with patch("tasks.brawl.field.BrawlVision.observe", return_value=contradictory):
            observed = read_field(self.frame)
        self.assertEqual(observed.heroes, ())
        self.assertFalse(observed.identity_complete)
        self.assertFalse(observed.complete)
        self.assertIn("exceed", observed.reason)

    def test_overlays_and_formation_controls_do_not_authorize_field_identity(self):
        for sample in ("move-source", "upgrade", "combat", "income-tooltip", "normal-victory15"):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observed = read_field(self.frame)
                self.assertEqual(observed.heroes, ())
                self.assertIsNone(observed.unknown_count)
                self.assertFalse(observed.identity_complete)

    def test_translated_sprite_updates_its_pixel_anchor_without_cached_offsets(self):
        self.frame = frame("field-glen")
        original = self.frame.copy()
        self.frame[270:425, 690:780] = 0
        self.frame[270:425, 566:656] = original[270:425, 690:780]
        try:
            FIELD_GLEN.load_offset((19, -13))
            observed = read_field(self.frame)
        finally:
            FIELD_GLEN.load_offset((0, 0))
        self.assertEqual(len(observed.heroes), 1)
        self.assertEqual(observed.heroes[0].foot, (607, 409))
        self.assertEqual(observed.heroes[0].area, (590, 287, 628, 350))

    def test_invalid_image_or_unsupported_server_is_rejected(self):
        for invalid in (None, np.zeros((360, 640, 3), dtype=np.uint8),
                        np.zeros((720, 1280, 3), dtype=np.float32)):
            with self.subTest(shape=getattr(invalid, "shape", None)), self.assertRaises(ValueError):
                read_field(invalid)
        self.frame = frame("field-glen")
        try:
            server.set_lang("global_en")
            with self.assertRaises(ValueError):
                read_field(self.frame)
        finally:
            server.set_lang("global_cn")


class FieldTraitRecognitionTests(unittest.TestCase):
    def setUp(self):
        server.set_lang("global_cn")

    def test_xiaochun_headers_survive_independent_idle_poses(self):
        # The asset comes from full-swap-after. These independent captures
        # include poses whose head/torso template no longer matches.
        for sample in ("full-swap-stable", "upper-summon-return", "upper-summon-stable"):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observed = read_field_traits(self.frame)
                known = [item for item in observed.headers if item.name == "小春"]
                self.assertEqual(len(known), 1)
                self.assertEqual((known[0].faction, known[0].profession), ("卡厄思", "战士"))
                self.assertEqual(known[0].area, (781, 426, 835, 451))
                self.assertFalse(hasattr(known[0], "foot"))
                self.assertEqual(observed.unknown_count, 9)
                self.assertFalse(observed.identity_complete)

    def test_independent_headers_identify_glen_and_maya(self):
        for sample, names in (("field-five-next", ["格伦", "战斗型玛雅"]),
                              ("full-bench-repeat", ["战斗型玛雅"])):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observed = read_field_traits(self.frame)
                self.assertEqual([item.name for item in observed.headers if item.name], names)

    def test_shared_combinations_keep_both_candidates(self):
        self.frame = frame("field-five-next")
        observed = read_field_traits(self.frame)
        ambiguous = {item.faction: item for item in observed.headers if item.name is None}
        self.assertEqual(ambiguous["人造生命体"].candidates, ("辅助型拉兹", "玫勒赛德丝"))
        self.assertEqual(ambiguous["夏日假期"].candidates, ("赛兹", "卡琳"))
        self.assertEqual(observed.unknown_count, 3)
        self.assertFalse(observed.identity_complete)

    def test_missing_or_redacted_symbol_does_not_reuse_prior_identity(self):
        self.frame = frame("full-swap-stable")
        self.assertIn("小春", [item.name for item in read_field_traits(self.frame).headers])
        for box in ((781, 426, 807, 451), (810, 426, 835, 451), (790, 430, 801, 444)):
            with self.subTest(box=box):
                self.frame = frame("full-swap-stable")
                x1, y1, x2, y2 = box
                self.frame[y1:y2, x1:x2] = 0
                observed = read_field_traits(self.frame)
                self.assertNotIn("小春", [item.name for item in observed.headers])
                self.assertEqual(observed.unknown_count, 10)

    def test_nonadjacent_symbols_are_not_combined(self):
        self.frame = frame("field-glen")
        icon = self.frame[251:280, 747:773].copy()
        self.frame[251:280, 747:773] = 0
        self.frame[251:280, 790:816] = icon
        observed = read_field_traits(self.frame)
        self.assertEqual(observed.headers, ())
        self.assertEqual(observed.unknown_count, 1)

    def test_overlays_and_bench_only_frames_do_not_supply_headers(self):
        for sample in ("full-swap-grid", "upgrade", "combat", "income-tooltip", "queue-top"):
            with self.subTest(sample=sample):
                self.frame = frame(sample)
                observed = read_field_traits(self.frame)
                self.assertEqual(observed.headers, ())
                if sample == "queue-top":
                    self.assertEqual(observed.unknown_count, 0)
                    self.assertTrue(observed.identity_complete)
                else:
                    self.assertIsNone(observed.unknown_count)
                    self.assertFalse(observed.identity_complete)

    def test_header_count_contradiction_rejects_complete_identity(self):
        self.frame = frame("field-glen")
        self.frame[251:280, 595:649] = self.frame[251:280, 719:773]
        observed = read_field_traits(self.frame)
        self.assertEqual(observed.headers, ())
        self.assertEqual(observed.unknown_count, 1)
        self.assertFalse(observed.identity_complete)
        self.assertIn("exceed", observed.reason)

    def test_translated_headers_report_only_current_symbol_location(self):
        self.frame = frame("field-glen")
        header = self.frame[251:280, 719:773].copy()
        self.frame[251:280, 719:773] = 0
        self.frame[251:280, 595:649] = header
        observed = read_field_traits(self.frame)
        self.assertEqual(len(observed.headers), 1)
        self.assertEqual(observed.headers[0].name, "格伦")
        self.assertEqual(observed.headers[0].area, (595, 251, 649, 280))
        self.assertTrue(observed.identity_complete)
        self.assertFalse(hasattr(observed.headers[0], "imprint"))
        self.assertFalse(hasattr(observed.headers[0], "foot"))

    def test_darkened_header_is_not_accepted_by_shape_alone(self):
        self.frame = frame("field-glen")
        self.frame[251:280, 719:773] //= 2
        observed = read_field_traits(self.frame)
        self.assertEqual(observed.headers, ())
        self.assertEqual(observed.unknown_count, 1)

    def test_transparent_shield_spaces_ignore_changed_background_colors(self):
        # Manually checked empty quarters INSIDE the shield, not a copy of
        # the production support mask. The white quarters and outline remain
        # original independent-capture pixels while the scenery is replaced.
        original = frame("full-bench-repeat")
        for color in ((255, 0, 255), (0, 255, 255), (245, 245, 245), (30, 40, 60)):
            with self.subTest(background=color):
                self.frame = original.copy()
                self.frame[452:456, 813:819] = color
                self.frame[460:464, 823:829] = color
                observed = read_field_traits(self.frame)
                maya = [header for header in observed.headers if header.name == "战斗型玛雅"]
                self.assertEqual(len(maya), 1)
                self.assertEqual(maya[0].area, (781, 445, 835, 472))

    def test_removed_shield_ink_and_flat_blocks_never_identify_maya(self):
        original = frame("full-bench-repeat")
        for color in ((255, 255, 255), (80, 90, 110), (0, 0, 0)):
            with self.subTest(block=color):
                self.frame = original.copy()
                self.frame[445:472, 810:835] = color
                self.assertNotIn("战斗型玛雅", [item.name for item in read_field_traits(self.frame).headers])
        self.frame = original.copy()
        # Erase a real white shield quarter while preserving the remaining
        # artwork and transparent map regions around the symbol.
        self.frame[452:457, 823:829] = (80, 90, 110)
        self.assertNotIn("战斗型玛雅", [item.name for item in read_field_traits(self.frame).headers])

    def test_different_profession_is_read_as_its_own_combination(self):
        self.frame = frame("full-bench-repeat")
        mage = frame("queue-empty")[214:241, 810:835]
        self.frame[445:472, 810:835] = mage
        observed = read_field_traits(self.frame)
        replaced = [item for item in observed.headers if item.area == (781, 445, 835, 472)]
        self.assertEqual(len(replaced), 1)
        self.assertIsNone(replaced[0].name)
        self.assertEqual(replaced[0].candidates, ("辅助型拉兹", "玫勒赛德丝"))
        self.assertNotIn("战斗型玛雅", [item.name for item in observed.headers])

    def test_duplicate_unique_headers_are_unknown_even_with_enough_deployed_slots(self):
        self.frame = frame("field-glen")
        current = BrawlVision(self.frame).observe()
        self.frame[251:280, 595:649] = self.frame[251:280, 719:773]
        current = replace(current, panel=replace(current.panel, deployed=2))
        observed = read_field_traits(self.frame, observation=current)
        self.assertEqual(observed.headers, ())
        self.assertEqual(observed.unknown_count, 2)
        self.assertFalse(observed.identity_complete)
        self.assertIn("multiple", observed.reason)

    def test_invalid_images_and_unsupported_servers_are_rejected(self):
        for invalid in (None, np.zeros((360, 640, 3), dtype=np.uint8),
                        np.zeros((720, 1280, 3), dtype=np.float32)):
            with self.subTest(shape=getattr(invalid, "shape", None)), self.assertRaises(ValueError):
                read_field_traits(invalid)
        self.frame = frame("field-glen")
        try:
            server.set_lang("global_en")
            with self.assertRaises(ValueError):
                read_field_traits(self.frame)
        finally:
            server.set_lang("global_cn")

"""Pixel equivalence, tolerance boundaries and preserved local OCR fixes."""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

import cv2
import numpy as np

from module.base.base import ModuleBase
from module.base.button import Button
from module.base.utils import (
    color_mask,
    color_similarity_2d,
    extract_letters,
    extract_white_letters,
)
from module.exception import ScriptError
from module.ocr.keyword import Keyword, parse_name
from module.ocr.ocr import BoxedResult, Duration, Ocr, OcrResultButton


def distance(image, color):
    difference = image.astype(np.int16) - np.array(color, dtype=np.int16)
    positive = np.maximum(difference, 0).max(axis=2)
    negative = np.maximum(-difference, 0).max(axis=2)
    return np.minimum(positive + negative, 255).astype(np.uint8)


class ColorTests(unittest.TestCase):
    def test_small_and_large_color_paths_match_integer_reference(self):
        rng = np.random.default_rng(20260928)
        for shape in ((17, 31, 3), (149, 200, 3), (150, 200, 3), (240, 320, 3)):
            image = rng.integers(0, 256, shape, dtype=np.uint8)
            original = image.copy()
            for color in ((0, 0, 0), (255, 255, 255), (33, 182, 90)):
                expected = distance(image, color)
                with self.subTest(shape=shape, color=color):
                    np.testing.assert_array_equal(color_similarity_2d(image, color), 255 - expected)
                    for threshold in (0, 5, 30, 34, 254, 255):
                        np.testing.assert_array_equal(color_mask(image, color, threshold),
                                                      np.where(expected <= threshold, 255, 0).astype(np.uint8))
                    np.testing.assert_array_equal(image, original)

    def test_letter_extraction_preserves_saturation_and_rounding(self):
        rng = np.random.default_rng(2)
        for shape in ((29, 40, 3), (200, 200, 3)):
            image = rng.integers(0, 256, shape, dtype=np.uint8)
            for color in ((255, 255, 255), (30, 180, 90)):
                for threshold in (127, 128, 255, 300):
                    with self.subTest(shape=shape, color=color, threshold=threshold):
                        # Scale only after clipping, as in the pre-backport implementation.
                        expected = cv2.convertScaleAbs(distance(image, color), alpha=255.0 / threshold)
                        np.testing.assert_array_equal(extract_letters(image, color, threshold), expected)

    def test_white_extraction_preserves_intermediate_half_rounding(self):
        values = np.arange(256, dtype=np.uint8)
        low, high = np.meshgrid(values, values)
        image = np.stack((low, high, np.full_like(low, 128)), axis=2)
        inverted = 255 - image
        maximum = cv2.convertScaleAbs(inverted.max(axis=2), alpha=0.5)
        minimum = cv2.convertScaleAbs(inverted.min(axis=2), alpha=0.5)
        original = np.minimum(2 * maximum.astype(np.int16) - minimum, 255).astype(np.uint8)
        for threshold in (127, 128, 255, 300):
            with self.subTest(threshold=threshold):
                expected = cv2.convertScaleAbs(original, alpha=255.0 / threshold)
                np.testing.assert_array_equal(extract_white_letters(image, threshold), expected)

    def test_color_count_uses_inclusive_distance_and_strict_pixel_count(self):
        module = object.__new__(ModuleBase)
        pixels = np.array([[[130, 100, 100], [131, 100, 100], [134, 100, 100]]], dtype=np.uint8)
        self.assertTrue(module.image_color_count(pixels, (100, 100, 100), count=0))
        self.assertFalse(module.image_color_count(pixels, (100, 100, 100), count=1))
        self.assertTrue(module.image_color_count(pixels, (100, 100, 100), threshold=34, count=2))

    def test_button_new_boundary_and_legacy_keyword(self):
        module = object.__new__(ModuleBase)
        module.device = SimpleNamespace(image=np.full((20, 20, 3), (105, 100, 100), dtype=np.uint8))
        area, color = (0, 0, 20, 20), (100, 100, 100)
        self.assertIsNotNone(module.image_color_button(area, color, encourage=2))
        self.assertIsNone(module.image_color_button(area, color, color_threshold=250, encourage=2))
        self.assertIsNotNone(module.image_color_button(area, color, color_threshold=249, encourage=2))

    def test_equal_sized_black_frame_does_not_match_template(self):
        button = object.__new__(Button)
        button.area = button.search = button._button = (0, 0, 20, 20)
        button._button_offset = (0, 0)
        button.image = np.random.default_rng(7).integers(0, 256, (20, 20, 3), dtype=np.uint8)
        black = np.zeros_like(button.image)
        self.assertFalse(button.match_template(black))
        self.assertFalse(button.match_template_luma(black))
        self.assertEqual(button.match_multi_template(black), [])
        self.assertTrue(button.match_template(button.image))
        self.assertTrue(button.match_template_luma(button.image))


class OcrTests(unittest.TestCase):
    def test_keyword_rebinding_restores_original_text_and_box(self):
        boxed = BoxedResult((10, 20, 50, 60), None, 'original', 0.9)
        button = OcrResultButton(boxed, None)
        button.set_matched_keyword('matched')
        self.assertTrue(button.is_keyword_matched)
        self.assertEqual(button.name, 'matched')
        button.set_matched_keyword(None)
        self.assertFalse(button.is_keyword_matched)
        self.assertEqual(button.name, 'original')
        self.assertEqual(button.area, boxed.box)
        self.assertIs(button.boxed_result, boxed)

    def test_batch_extension_is_used_and_filters_unmatched_results(self):
        ocr = object.__new__(Ocr)
        ocr.name = 'offline'
        boxed = [BoxedResult((10, 20, 50, 60), None, text, 0.9) for text in ('known', 'unknown')]
        ocr.detect_and_ocr = Mock(return_value=boxed)
        ocr._match_result = Mock(side_effect=['keyword', None])
        original_batch = ocr._product_buttons
        ocr._product_buttons = Mock(wraps=original_batch)
        results = ocr.matched_ocr(None, keyword_classes='class', lang='en', ignore_punctuation=False)
        self.assertEqual([item.name for item in results], ['keyword'])
        ocr._product_buttons.assert_called_once_with(boxed, keyword_classes='class', lang='en',
                                                     ignore_punctuation=False)
        self.assertFalse(ocr._match_result.call_args.kwargs['ignore_punctuation'])
        ocr._match_result.reset_mock(side_effect=True)
        original_batch(boxed, 'class', ignore_digit=False)
        self.assertFalse(ocr._match_result.call_args.kwargs['ignore_digit'])

    def test_local_colon_duration_formats_are_preserved(self):
        ocr = object.__new__(Duration)
        ocr.lang = 'cn'
        for text, expected in (('13:53', timedelta(minutes=13, seconds=53)),
                               ('05：13：53', timedelta(hours=5, minutes=13, seconds=53)),
                               ('2小时13分钟', timedelta(hours=2, minutes=13))):
            with self.subTest(text=text):
                self.assertEqual(ocr.format_result(ocr.after_process(text)), expected)


class OcrCorrectionTests(unittest.TestCase):
    def keywords(self):
        class OfflineKeyword(Keyword):
            instances: ClassVar = {}

        guild = OfflineKeyword(1, 'Guild', '骑士团', 'Guild', 'ギルド', '騎士團', 'Gremio')
        shop = OfflineKeyword(2, 'Shop', '商店', 'Shop', 'ショップ', '商店', 'Tienda')
        return OfflineKeyword, guild, shop

    def ocr(self, lang='cn', replacements=None, matches=None):
        parser_type = type('OfflineOcr', (Ocr,), {
            'OCR_REPLACE': replacements or {}, 'OCR_MATCH': matches or {},
        })
        return parser_type(SimpleNamespace(name='offline', area=(10, 20, 90, 60)), lang=lang)

    def test_unconfigured_parser_preserves_text_and_uid_redaction(self):
        parser = Ocr(SimpleNamespace(name='offline'), lang='cn')
        for text in ('', '骑士团', 'unknown 123', '13:53', '★'):
            with self.subTest(text=text):
                self.assertEqual(parser.after_process(text), text)
        self.assertEqual(parser.after_process('UID123456789'), 'UID')

    def test_regex_replacements_run_in_order_with_backreferences(self):
        parser = self.ocr(replacements={'cn': [(r'公[会曾]', '骑士团'),
                                               (r'(骑士团)\d+', r'\1'),
                                               (r'当前持有.*', '')]})
        self.assertEqual(parser.after_process('公曾12当前持有34'), '骑士团')
        self.assertEqual(parser.after_process('无关文本'), '无关文本')

    def test_replacement_and_match_rules_are_isolated_by_language(self):
        _, guild, _ = self.keywords()
        replacements = {'cn': [('公会', '骑士团')], 'en': [('Gulld', 'Guild')]}
        matches = {'cn': [(guild, '骑士团')], 'en': [(guild, 'Guild')]}
        for lang, text, expected, untouched in (
                ('cn', '公会', '骑士团', 'Gulld'),
                ('en', 'Gulld', 'Guild', '公会'),
                ('jp', '公会', '公会', 'Gulld')):
            with self.subTest(lang=lang):
                parser = self.ocr(lang, replacements, matches)
                self.assertEqual(parser.after_process(text), expected)
                self.assertEqual(parser.after_process(untouched), untouched)

    def test_fullmatch_uses_current_language_after_replacement(self):
        _, guild, shop = self.keywords()
        parser = self.ocr('en', {'en': [('Gulld', 'Guild')]},
                          {'en': [(guild, 'Guild'), (shop, r'Sh[o0]p')]})
        self.assertEqual(parser.after_process('Gulld'), 'Guild')
        self.assertEqual(parser.after_process('Sh0p'), 'Shop')
        for text in ('Guild extra', 'extra Sh0p', 'unknown', ''):
            with self.subTest(text=text):
                self.assertEqual(parser.after_process(text), text)
        localized = self.ocr('cn', matches={'cn': [(guild, 'Guild')]})
        self.assertEqual(localized.after_process('Guild'), '骑士团')

    def test_first_matching_rule_wins_and_subclasses_keep_their_rules(self):
        _, guild, shop = self.keywords()
        first = self.ocr(matches={'cn': [(guild, '通用'), (shop, '通用')]})
        second = self.ocr(matches={'cn': [(shop, '通用')]})
        self.assertEqual(first.after_process('通用'), '骑士团')
        self.assertEqual(second.after_process('通用'), '商店')
        self.assertEqual(first.after_process('通用'), '骑士团')
        self.assertEqual(Ocr(SimpleNamespace(name='default'), lang='cn').after_process('通用'), '通用')

    def test_single_and_batch_entry_points_apply_corrections(self):
        _, guild, _ = self.keywords()
        parser = self.ocr('en', {'en': [('Gulld', 'Guild')]}, {'en': [(guild, 'Guild')]})
        image = np.zeros((80, 100, 3), dtype=np.uint8)
        parser.model = SimpleNamespace(
            ocr_single_line=Mock(return_value=('Gulld', 0.8)),
            ocr_lines=Mock(return_value=[('Gulld', 0.8), ('unknown', 0.7)]),
        )
        self.assertEqual(parser.ocr_single_line(image), 'Guild')
        self.assertEqual(parser.model.ocr_single_line.call_args.args[0].shape, (40, 80, 3))
        self.assertEqual(parser.ocr_multi_lines([image, image]), [('Guild', 0.8), ('unknown', 0.7)])

    def test_detected_keywords_keep_coordinates_and_filter_unknown_text(self):
        keyword_type, guild, shop = self.keywords()
        parser = self.ocr('en', {'en': [('Gulld', 'Guild')]},
                          {'en': [(guild, 'Guild'), (shop, r'Sh[o0]p')]})
        boxes = [BoxedResult(np.array([[1, y], [30, y], [30, y + 5], [1, y + 5]]), None, text, score)
                 for y, text, score in ((1, 'Gulld', 0.8), (12, 'Sh0p', 0.7), (23, 'unknown', 0.6))]
        parser.model = SimpleNamespace(detect_and_ocr=Mock(return_value=boxes))
        results = parser.matched_ocr(np.zeros((80, 100, 3), dtype=np.uint8), keyword_type, lang='en')
        self.assertEqual([button.matched_keyword for button in results], [guild, shop])
        self.assertEqual([button.text for button in results], ['Guild', 'Shop'])
        self.assertEqual([button.area for button in results], [(11, 21, 40, 26), (11, 32, 40, 37)])
        self.assertEqual([button.score for button in results], [0.8, 0.7])

    def test_star_is_ignored_only_when_punctuation_is_ignored(self):
        keyword_type, guild, _ = self.keywords()
        self.assertEqual(parse_name('★ Guild・'), 'guild')
        self.assertIs(keyword_type.find('★Guild', lang='en'), guild)
        self.assertIs(keyword_type.find('★骑士团', lang='cn'), guild)
        with self.assertRaises(ScriptError):
            keyword_type.find('★Guild', lang='en', ignore_punctuation=False)
        with self.assertRaises(ScriptError):
            keyword_type.find('Other★Guild', lang='en')


if __name__ == '__main__':
    unittest.main()

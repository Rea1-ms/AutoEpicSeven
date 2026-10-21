"""Pixel equivalence, tolerance boundaries and preserved local OCR fixes."""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from module.base.base import ModuleBase
from module.base.button import Button
from module.base.utils import color_mask, color_similarity_2d, extract_letters, extract_white_letters
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


if __name__ == '__main__':
    unittest.main()

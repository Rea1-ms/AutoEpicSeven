"""Reuse exact name pixels while reading rank, cost and HP from every frame."""

from collections import OrderedDict

from module.base.button import ClickButton
from module.base.utils import area_offset
from module.logger import logger
from module.ocr.ocr import Ocr
from pponnxcr.predict_system import BoxedResult


class HeroNameOcr:
    def __init__(self, capacity=128):
        self.capacity = capacity
        self._entries = OrderedDict()

    def read(self, image, area, name):
        x1, y1, x2, y2 = area
        pixels = image[y1:y2, x1:x2]
        # Full pixel equality is required, including background and selection
        # changes. Never reuse by hero identity, row number or partial hashes.
        key = (pixels.shape, pixels.dtype.str, pixels.tobytes())
        if key in self._entries:
            entries = self._entries[key]
            self._entries.move_to_end(key)
            logger.attr(f"{name} cached", [text for _, text, _ in entries])
            return [BoxedResult(area_offset(box, (x1, y1)), None, text, score)
                    for box, text, score in entries]
        results = Ocr(ClickButton(area, name=name), lang="cn", name=name).detect_and_ocr(image)
        # Ambiguous or failed reads remain uncached. Each result has a fresh
        # click box translated to the observed frame, never an old coordinate.
        if len(results) == 1 and results[0].ocr_text:
            self._entries[key] = tuple((area_offset(result.box, (-x1, -y1)), result.ocr_text, result.score)
                                       for result in results)
            if len(self._entries) > self.capacity:
                self._entries.popitem(last=False)
        return results

"""Read server-repeat progress without treating estimates as completion."""

import math
import re
from dataclasses import dataclass
from datetime import timedelta

from module.ocr.ocr import Duration, Ocr
from tasks.dungeon.assets.assets_dungeon_repeat_settlement import (
    OCR_SETTLEMENT_PROGRESS,
    OCR_SETTLEMENT_TIME_SPENT,
)


class RepeatProgressOcr(Ocr):
    def format_result(self, result: str) -> tuple[int, int] | None:
        text = re.sub(r"\s+", "", result).replace("／", "/")
        matched = re.fullmatch(r"(?:战斗进度[:：]?)?([0-9]+)/([0-9]+)", text)
        if matched is None:
            return None
        completed, total = map(int, matched.groups())
        if total <= 0 or not 0 <= completed <= total:
            return None
        return completed, total


class RepeatElapsedOcr(Duration):
    def format_result(self, result: str) -> timedelta | None:
        # This field is elapsed HH:MM:SS, not a countdown. Reject missing
        # components and out-of-range minutes/seconds rather than allowing
        # timedelta to normalize an OCR error into a much later wake-up.
        text = re.sub(r"\s+", "", result).replace("：", ":")
        matched = re.fullmatch(r"([0-9]{2}):([0-5][0-9]):([0-5][0-9])", text)
        if matched is None:
            return None
        hours, minutes, seconds = map(int, matched.groups())
        return timedelta(hours=hours, minutes=minutes, seconds=seconds)


@dataclass(frozen=True)
class RepeatCombatProgress:
    completed: int
    total: int
    elapsed: timedelta

    @property
    def estimated_remaining(self) -> timedelta | None:
        # An empty sample cannot establish the per-battle duration. A full
        # counter is also not proof that rewards are ready: the game's result
        # controls must still confirm completion, so use short polling then.
        if not 0 < self.completed < self.total or self.elapsed.total_seconds() <= 0:
            return None
        seconds = self.elapsed.total_seconds() * (self.total - self.completed) / self.completed
        return timedelta(seconds=math.ceil(seconds))


def read_repeat_combat_progress(image, lang: str) -> RepeatCombatProgress | None:
    counter = RepeatProgressOcr(
        OCR_SETTLEMENT_PROGRESS, lang=lang, name="RepeatCombatProgress"
    ).ocr_single_line(image)
    elapsed = RepeatElapsedOcr(
        OCR_SETTLEMENT_TIME_SPENT, lang=lang, name="RepeatCombatElapsedTime"
    ).ocr_single_line(image)
    if counter is None or elapsed is None:
        return None
    return RepeatCombatProgress(*counter, elapsed)

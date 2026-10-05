import hashlib
import json
from pathlib import Path

import cv2

from module.base.utils import load_image


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "equipment_reroll"


def load_sample(sample_id):
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    samples = [sample for sample in manifest["samples"] if sample["id"] == sample_id]
    if len(samples) != 1:
        raise ValueError(f"样本编号不存在或重复：{sample_id}")
    sample = samples[0]
    path = (FIXTURES / sample["path"]).resolve()
    if not path.is_relative_to(FIXTURES.resolve()):
        raise ValueError("样本路径越界")
    if hashlib.sha256(path.read_bytes()).hexdigest() != sample["sha256"]:
        raise ValueError(f"样本校验失败：{sample_id}")
    image = load_image(path)
    if image.shape != (720, 1280, 3):
        raise ValueError(f"样本尺寸错误：{sample_id}")
    return image


def with_point_balance(image, points):
    """Synthetic balance glyphs only; source workshop controls stay unchanged."""
    result = image.copy()
    result[102:140, 975:1085] = (25, 22, 28)
    region = result[102:140, 975:1085]
    cv2.putText(region, f"{points:,}", (1, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (235, 190, 60), 1, cv2.LINE_AA)
    return result


class ReplayDevice:
    """Clicks record intentions only; frames advance exclusively on screenshot."""

    def __init__(self, frames, clock=None):
        self.frames = list(frames)
        self.image = self.frames.pop(0)
        self.actions = []
        self.click_areas = []
        self.action_times = []
        self.clock = clock
        self.click_history_clears = 0
        self.stuck_history_clears = 0

    def screenshot(self):
        if not self.frames:
            raise AssertionError(f"回放帧已用完，流程仍未退出；动作：{self.actions}")
        self.image = self.frames.pop(0)
        if self.clock is not None:
            self.clock.advance()

    def click(self, button):
        self.actions.append(button.name)
        self.click_areas.append(button.button)
        self.action_times.append(None if self.clock is None else self.clock())

    def stuck_record_add(self, button):
        pass

    def click_record_clear(self):
        self.click_history_clears += 1

    def stuck_record_clear(self):
        self.stuck_history_clears += 1


class ReplayClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def advance(self, seconds=1):
        self.now += seconds

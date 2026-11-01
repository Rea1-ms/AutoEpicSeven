"""Registered handbook captures; explicitly synthetic daily states when needed."""

from types import SimpleNamespace

from module.base.base import ModuleBase
from tests.support.interaction import InteractionReplay, scene
from tests.support.offline import ROOT, fixture_image, record_frame

MANIFEST = ROOT / "tests/fixtures/historical/manifest.json"
CAPTURES = ("000455-285", "000500-016", "000510-920", "000516-161", "000524-767",
            "000527-127", "000533-874", "000540-850", "000545-575", "000555-014")
HOME, HUB, BOOK, READY, POPUP, AFTER, BOTTOM, BOOK_AFTER, HUB_AFTER, HOME_AFTER = CAPTURES


def capture(name=READY, **values):
    page = ("page_main" if name in (HOME, HOME_AFTER) else
            "page_alencia_hub" if name in (HUB, HUB_AFTER) else
            "page_alencia_handbook" if name in (BOOK, BOOK_AFTER) else
            "page_alencia_rewards" if name != POPUP else None)
    return scene(fixture=f"{name}", page=page, **values)


def daily_results(states):
    """Synthetic OCR only; row geometry matches the captured top two cards."""
    results = []
    for index, (tier, state) in enumerate(states.items()):
        top = 120 + index * 123
        current = tier if state in ("pending", "received") else 0
        values = [(f"【每日成就】使用{tier}点行动力（1光谱核心=160）", (476, top, 860, top + 20)),
                  (f"{current}/{tier}", (623, top + 42, 687, top + 62))]
        if state != "received":
            values.append(("领取" if state == "pending" else "进行中", (1115, top + 4, 1183, top + 33)))
        results.extend(SimpleNamespace(ocr_text=text, box=box, score=0.99) for text, box in values)
    return results


def chapter_results():
    """Synthetic top cards after both dailies leave the unclaimed section."""
    results = daily_results({200: "pending", 400: "pending"})
    results[0].ocr_text = "进行事件手册第1章线索搜集8次"
    results[1].ocr_text = "8/8"
    results[3].ocr_text = "完成事件手册第1章"
    results[4].ocr_text = "1/1"
    return results


class HandbookReplay(InteractionReplay):
    def __init__(self, frames, clock):
        self.images, self.ocr_cache, self.click_areas = {}, {}, []
        super().__init__(self.task_type(), frames, clock)
        self.task.activity_id = "alencia_handbook_2026_11_01"
        self.task.config.SpecialActivity_GetAlenciaDailyReward = True
        self.task.config.is_task_enabled = lambda task: True
        self.task.match_template_color = self.match_template_color
        self.task.match_template_luma = self.match_template_luma
        self.task.ui_page_confirm = lambda page: False
        self.task.handle_ui_recovery = lambda: False
        self.task.ui_button_interval_reset = lambda button: None
        self.task.sync_pending_resources = lambda: None

    @staticmethod
    def task_type():
        from tasks.activity.alencia_handbook import AlenciaHandbook

        return AlenciaHandbook

    def screenshot(self):
        super().screenshot()
        key = self.frame["fixture"]
        if key not in self.images:
            self.images[key] = fixture_image(MANIFEST, key)
        self.image = self.images[key].copy()
        if "ocr" in self.frame:
            record_frame("synthetic:handbook-explicit-ocr")
        if "daily" in self.frame:
            received = fixture_image(MANIFEST, f"{BOTTOM}")
            for index, state in enumerate(self.frame["daily"].values()):
                top = 120 + index * 123
                self.image[top - 20:top + 65, 1055:1248] = 0
                if state == "received":
                    self.image[top + 8:top + 33, 1136:1167] = received[399:424, 1136:1167]
            record_frame("synthetic:handbook-daily-controls-and-ocr")

    def ocr_results(self, image):
        if "ocr" in self.frame:
            return self.frame["ocr"]
        if "daily" in self.frame:
            return daily_results(self.frame["daily"])
        key = self.frame["fixture"]
        if key not in self.ocr_cache:
            from module.ocr.ocr import Ocr
            from tasks.activity.assets.assets_activity_alencia_handbook import (
                ALENCIA_TASK_LIST,
            )

            self.ocr_cache[key] = Ocr(ALENCIA_TASK_LIST, lang="cn").detect_and_ocr(image)
        return self.ocr_cache[key]

    def appear(self, button, interval=0, **kwargs):
        return ModuleBase.appear(self.task, button, interval=interval, **kwargs)

    def match_template_color(self, button, interval=0, **kwargs):
        return ModuleBase.match_template_color(self.task, button, interval=interval, **kwargs)

    def match_template_luma(self, button, interval=0, **kwargs):
        return ModuleBase.match_template_luma(self.task, button, interval=interval, **kwargs)

    def click(self, button):
        super().click(button)
        self.click_areas.append((self.index, button.name, tuple(button.button)))

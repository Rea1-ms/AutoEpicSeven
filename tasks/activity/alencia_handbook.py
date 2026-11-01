"""Daily stamina rewards in Alencia's handbook, entered from the home hub."""

import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_alencia_handbook import (
    ALENCIA_HANDBOOK_CHECK,
    ALENCIA_HUB_CHECK,
    ALENCIA_REWARD_CLOSE,
    ALENCIA_REWARD_POPUP_CHECK,
    ALENCIA_REWARDS_CHECK,
    ALENCIA_TASK_LIST,
    ALENCIA_TASK_RECEIVED,
)
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.assets.assets_base_page import BACK
from tasks.base.ui import UI


class AlenciaHandbook(UI):
    """Claim only the 200/400 stamina dailies, including their combined claim."""

    DAILY_TIERS = frozenset((200, 400))
    CLAIM_FLOW_TIMEOUT_SECONDS = 90

    def __init__(self, config, device=None, task=None,
                 activity_id="alencia_handbook_2026_11_01"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def _received_in_row(self, area):
        searches = tuple(frame.search for frame in ALENCIA_TASK_RECEIVED.iter_buttons())
        try:
            ALENCIA_TASK_RECEIVED.load_search(area)
            return self.match_template_color(ALENCIA_TASK_RECEIVED)
        finally:
            for frame, search in zip(ALENCIA_TASK_RECEIVED.iter_buttons(), searches):
                frame.load_search(search)
                frame.clear_offset()

    @staticmethod
    def _top_titles(results):
        """Read only the title bands of the first two fixed-position cards."""
        return [label for label in results
                if label.score >= 0.9 and 450 <= label.box[0] < label.box[2] <= 880
                and any(105 + offset <= label.box[1] < label.box[3] <= 155 + offset
                        for offset in (0, 123))]

    def _chapter_at_top(self, results):
        # Unclaimed dailies precede every chapter task. A positively read
        # chapter in the FIRST card therefore proves both dailies are done.
        # A lower chapter, missing daily text, or unreadable first card does
        # not prove this ordering boundary; never scroll to manufacture it.
        return any(label.box[3] <= 155 and re.fullmatch(
            r"(?:进行事件手册第\d+章线索搜集\d+次|完成事件手册第\d+章)",
            "".join(label.ocr_text.split()),
        ) for label in self._top_titles(results))

    def _daily_rows(self, results):
        """Bind a top daily identity, counter and control to the same card."""
        rows = {}
        for label in self._top_titles(results):
            text = "".join(label.ocr_text.split())
            # The daily prefix and stamina tier identify the task. The
            # explanatory conversion suffix may lose its closing bracket in
            # OCR; it must not invalidate an otherwise complete identity.
            match = re.fullmatch(
                r"[【\[]每日成就[】\]]使用(200|400)点行动力(?:[（(]1光谱核心[=＝]160[）)]?)?", text)
            if not match:
                continue
            tier = int(match[1])
            top, bottom = label.box[1] - 31, label.box[3] + 55
            if top < 80 or bottom > 719 or tier in rows:
                continue
            same_row = [item for item in results
                        if top <= item.box[1] and item.box[3] <= bottom]
            counts = [re.fullmatch(r"(\d+)\s*/\s*(\d+)", item.ocr_text)
                      for item in same_row if 580 < item.box[0] < item.box[2] < 730]
            counts = [item for item in counts if item is not None]
            if len(counts) != 1:
                continue
            current, total = map(int, counts[0].groups())
            if total != tier or not 0 <= current <= total:
                continue
            area = (1055, top, 1248, bottom)
            controls = {"".join(item.ocr_text.split()) for item in same_row
                        if item.box[0] >= 1055 and item.score >= 0.85}
            if current == tier and self._received_in_row(area):
                state = "received"
            elif current == tier and "领取" in controls:
                state = "pending"
            elif current < tier:
                # The daily identity and its own counter prove that it
                # is unfinished. A missed status word must not force a scan
                # past this known top-row daily or authorize a claim.
                state = "incomplete"
            else:
                continue
            rows[tier] = (state, area)
        return rows

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Check the top two tasks and leave the handbook through its hub.

        Pages:
            in: any; handbook achievements when navigate=False
            out: page_alencia_hub, reward popup closed
        """
        if navigate:
            # These pages only exist for the verified overseas Chinese client.
            from tasks.base.page import page_alencia_rewards

            self.ui_goto(page_alencia_rewards, skip_first_screenshot=skip_first_screenshot)
            skip_first_screenshot = True
        lang = self.config.Emulator_GameLanguage or "cn"
        if lang == "auto":
            lang = "cn"
        ocr = Ocr(ALENCIA_TASK_LIST, lang=lang, name="AlenciaDailyTasks")
        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS).start()
        pending_tier = None
        popup_seen = False
        result = None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if result is not None and self.match_template_color(ALENCIA_HUB_CHECK):
                if result:
                    mark_activity_checked(self.config, self.activity_id)
                else:
                    logger.info("SpecialActivity: handbook dailies unfinished, recheck after battle")
                return True
            if timeout.reached():
                logger.warning("SpecialActivity: handbook reward timeout")
                self.config.task_delay(success=False)
                return False

            if self.match_template_color(ALENCIA_REWARDS_CHECK):
                # One daily button can claim BOTH tiers. Preserve the request
                # until the actual reward popup has appeared AND the list has
                # returned. Reordering or a network dismissal is not a receipt.
                # Re-read the top after that receipt; old positions and old
                # incomplete states cannot finish the newly updated daily set.
                if popup_seen:
                    pending_tier = None
                    popup_seen = False
                if result is not None:
                    button = ClickButton(BACK.button, name="ALENCIA_REWARDS_BACK")
                    if self.interval_is_reached(button, interval=2):
                        self.device.click(button)
                        self.interval_reset(button, interval=2)
                        continue
                else:
                    results = ocr.detect_and_ocr(self.device.image)
                    rows = self._daily_rows(results)
                    candidates = [tier for tier, (state, _) in rows.items() if state == "pending"]
                    if pending_tier is None and not candidates:
                        # Entry and the returned claim list start at the top.
                        # Known unfinished dailies defer immediately; a first
                        # chapter card proves the sorted dailies are done.
                        # Unknown OCR waits for a new frame, never a swipe.
                        if any(state == "incomplete" for state, _ in rows.values()):
                            result = False
                        elif self._chapter_at_top(results) or (
                            rows.keys() == self.DAILY_TIERS
                            and all(state == "received" for state, _ in rows.values())
                        ):
                            result = True
                            logger.info("SpecialActivity: handbook top tasks confirm daily completion")
                        if result is not None:
                            button = ClickButton(BACK.button, name="ALENCIA_REWARDS_BACK")
                            self.device.click(button)
                            self.interval_reset(button, interval=2)
                            continue
                    if pending_tier is not None:
                        candidates = [pending_tier] if pending_tier in candidates else []
                    if candidates:
                        tier = min(candidates)
                        button = ClickButton(rows[tier][1], name=f"ALENCIA_CLAIM_{tier}")
                        # Only the same still-unclaimed daily may retry a
                        # dropped tap. Another tier/achievement never does.
                        if self.interval_is_reached(button, interval=2):
                            self.device.click(button)
                            self.interval_reset(button, interval=2)
                            pending_tier = tier
                            continue

            if result is not None and self.match_template_color(ALENCIA_HANDBOOK_CHECK):
                button = ClickButton(BACK.button, name="ALENCIA_HANDBOOK_BACK")
                if self.interval_is_reached(button, interval=2):
                    self.device.click(button)
                    self.interval_reset(button, interval=2)
                    continue
            if self.handle_network_error():
                continue
            if self.match_template_color(ALENCIA_REWARD_POPUP_CHECK):
                popup_seen = True
                if self.appear_then_click(ALENCIA_REWARD_CLOSE, interval=2):
                    continue

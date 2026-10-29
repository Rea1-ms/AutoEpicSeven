import re

from module.base.button import ClickButton
from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.activity.assets.assets_activity_epic_path import (
    EPIC_PATH_CHECK,
    EPIC_PATH_DAILY_TASK_CHECK,
    EPIC_PATH_REWARD_POPUP_CHECK,
    EPIC_PATH_TASK_CLOSE,
    EPIC_PATH_TASK_ENTRY,
    EPIC_PATH_TASK_LIST,
    EPIC_PATH_TASK_RECEIVED,
)
from tasks.activity.navigation import ActivityNavigationMixin
from tasks.activity.scheduling import mark_activity_checked
from tasks.base.page import page_common_activity
from tasks.base.ui import UI


class EpicPath(ActivityNavigationMixin, UI):
    """Collect the four daily tasks without opening the season or reward shop."""

    CLAIM_FLOW_TIMEOUT_SECONDS = 45
    DAILY_TASKS = frozenset((("arena", 3), ("arena", 5), ("stamina", 100), ("stamina", 200)))
    ROW_AREAS = ((193, 189, 1098, 290), (193, 308, 1098, 410),
                 (193, 427, 1098, 530), (193, 547, 1098, 650))

    def __init__(self, config, device=None, task=None, activity_id="epic_path_2026_10_29"):
        super().__init__(config=config, device=device, task=task)
        self.activity_id = activity_id

    def _received_in_row(self, area):
        # Claimed tasks move below unfinished tasks and have a check icon,
        # not a text label. A different row's receipt must never authorize
        # completion of the currently read task. Restore shared asset state.
        searches = tuple(frame.search for frame in EPIC_PATH_TASK_RECEIVED.iter_buttons())
        try:
            EPIC_PATH_TASK_RECEIVED.load_search(area)
            return self.match_template_color(EPIC_PATH_TASK_RECEIVED)
        finally:
            for frame, search in zip(EPIC_PATH_TASK_RECEIVED.iter_buttons(), searches):
                frame.load_search(search)
                frame.clear_offset()

    def _daily_rows(self, results):
        rows = {}
        for area in self.ROW_AREAS:
            _, top, _, bottom = area
            matches = [row for row in results if top <= row.box[1] and row.box[3] <= bottom]
            label = "".join(row.ocr_text for row in matches if row.box[2] < 810)
            label = "".join(label.split())
            count = re.search(r"(\d+)\s*/\s*(\d+)", label)
            family = "arena" if "竞技场" in label else "stamina" if "消耗行动力" in label else None
            if not count or family is None:
                return None
            current, total = map(int, count.groups())
            identity = (family, total)
            if identity not in self.DAILY_TASKS or identity in rows:
                return None
            controls = [row for row in matches if row.box[0] > 900 and row.score >= 0.9]
            texts = ["".join(row.ocr_text.split()) for row in controls]
            control_area = (904, top + 16, 1080, bottom - 15)
            if any(text in ("领取", "领取奖励") for text in texts) and current >= total:
                state = "pending"
            elif "前往" in texts and current < total:
                state = "incomplete"
            elif current >= total and self._received_in_row(control_area):
                state = "received"
            else:
                return None
            rows[identity] = (state, control_area)
        return rows if rows.keys() == self.DAILY_TASKS else None

    def run_claim(self, skip_first_screenshot=True, *, navigate=True) -> bool:
        """Read and claim daily tasks, returning with the task dialog closed.

        Pages:
            in: any; epic path selected when navigate=False
            out: page_common_activity, epic path, task and reward dialogs closed
        """
        if navigate:
            self.ui_goto(page_common_activity, skip_first_screenshot=skip_first_screenshot)
            if not self.select_activity("史诗之路", EPIC_PATH_CHECK):
                return False
            skip_first_screenshot = True
        else:
            if not skip_first_screenshot:
                self.device.screenshot()
                skip_first_screenshot = True
            if not self.match_template_color(EPIC_PATH_CHECK):
                return False
        lang = self.config.Emulator_GameLanguage or "cn"
        if lang == "auto":
            lang = "cn"
        ocr = Ocr(EPIC_PATH_TASK_LIST, lang=lang, name="EpicPathDailyTasks")
        timeout = Timer(self.CLAIM_FLOW_TIMEOUT_SECONDS).start()
        pending_task = None
        result = None
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if result is not None and self.match_template_color(EPIC_PATH_CHECK):
                if result:
                    mark_activity_checked(self.config, self.activity_id)
                else:
                    logger.info("LimitedActivity: epic daily tasks unfinished, recheck after battle")
                return True
            if timeout.reached():
                logger.warning("LimitedActivity: epic daily reward timeout")
                return False
            if self.match_template_color(EPIC_PATH_DAILY_TASK_CHECK):
                rows = self._daily_rows(ocr.detect_and_ocr(self.device.image))
                if rows is not None and result is None:
                    if pending_task is None and all(state != "pending" for state, _ in rows.values()):
                        result = all(state == "received" for state, _ in rows.values())
                    else:
                        candidates = [key for key, (state, _) in rows.items() if state == "pending"]
                        if pending_task is not None:
                            candidates = [pending_task] if pending_task in candidates else []
                        if candidates:
                            identity = candidates[0]
                            button = ClickButton(rows[identity][1], name=f"EPIC_CLAIM_{identity[0]}_{identity[1]}")
                            if self.interval_is_reached(button, interval=2):
                                self.device.click(button)
                                self.interval_reset(button, interval=2)
                                # Retain the task identity while its reward popup
                                # is delayed. Reordered rows never authorize a
                                # second reward before the first is confirmed.
                                pending_task = identity
                                continue
                if result is not None and self.appear_then_click(EPIC_PATH_TASK_CLOSE, interval=2):
                    continue
            if (result is None and self.match_template_color(EPIC_PATH_CHECK)
                    and self.appear_then_click(EPIC_PATH_TASK_ENTRY, interval=2)):
                continue
            if (result is None and not self.match_template_color(EPIC_PATH_DAILY_TASK_CHECK)
                    and self.match_template_luma(EPIC_PATH_TASK_CLOSE)
                    and self.match_template_luma(EPIC_PATH_DAILY_TASK_CHECK, interval=2)):
                # A previous manual visit may have left the season tab open.
                # Only the daily label is a navigation target; never process
                # season rows using the four-task daily completion contract.
                self.device.click(EPIC_PATH_DAILY_TASK_CHECK)
                continue
            if self.handle_network_error():
                continue
            # Confirm this event's fully visible reward title before clearing
            # the pending identity; network dismissal is not a reward receipt.
            if self.match_template_color(EPIC_PATH_REWARD_POPUP_CHECK) and self.handle_touch_to_close():
                pending_task = None

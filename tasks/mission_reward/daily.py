"""Read daily missions down to the completed-login boundary."""

import re
from dataclasses import dataclass

import cv2
import numpy as np

from module.base.timer import Timer
from module.base.utils import crop
from module.config import server
from module.logger import logger
from module.ocr.language import resolve_ocr_lang
from module.ocr.ocr import Ocr
from tasks.base.page import page_mission_reward_daily
from tasks.mission_reward.assets.assets_mission_reward_daily_list import (
    OCR_DAILY_MISSIONS,
)


@dataclass(frozen=True)
class DailyMission:
    key: str
    current: int
    total: int
    points: int


DAILY_MISSION_SPECS = {
    "stamina_50": (50, 20), "stamina_100": (100, 20), "stamina_150": (150, 20),
    "core_1": (1, 20), "core_2": (2, 20), "core_3": (3, 20),
    "arena_5": (5, 30), "arena_10": (10, 30), "gacha": (1, 15),
    "flower": (1, 15), "hero": (1, 15), "equipment": (1, 10), "login": (1, 25),
}
DAILY_MISSION_KEYS = frozenset(DAILY_MISSION_SPECS)


def _mission_key(title: str, total: int) -> str:
    # Roman numerals are frequently dropped or confused by OCR. The complete
    # fraction's denominator identifies the tier; title text identifies its
    # meaning, so an unrelated numerical field can never supply the tier.
    for label, prefix, totals in (
        ("使用行动力", "stamina", (50, 100, 150)),
        ("使用光谱核心", "core", (1, 2, 3)),
        ("自我修炼", "arena", (5, 10)),
    ):
        if label in title and total in totals:
            return f"{prefix}_{total}"
    for label, key in (
        ("誓约召唤", "gacha"), ("圣约召唤", "gacha"), ("培育精灵之花", "flower"),
        ("英雄升等", "hero"), ("强化装备", "equipment"), ("登录", "login"),
    ):
        if label in title and total == 1:
            return key
    normalized_title = re.sub(r"\s+", "", title)
    return f"unknown:{normalized_title}:{total}"


def _counter(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", text)
    if match is None:
        return None
    current, total = (int(value) for value in match.groups())
    if 0 <= current <= total <= 999 and total > 0:
        return current, total
    return None


class DailyMissionMixin:
    DAILY_SCAN_TIMEOUT_SECONDS = 90
    DAILY_SCROLL_INTERVAL_SECONDS = 2
    DAILY_SCAN_MAX_FRAMES = 256
    DAILY_SCAN_MAX_DOWN_SWIPES = 16
    DAILY_SCAN_STABLE_FRAMES = 2

    def _read_daily_missions(self) -> tuple[DailyMission, ...] | None:
        """Read complete rows in the current frame; unreadable rows stay unknown."""
        if server.lang not in ("cn", "global_cn"):
            return None
        if not self.match_template_color(page_mission_reward_daily.check_button):
            return None

        image = self.device.image
        ocr = Ocr(OCR_DAILY_MISSIONS, lang=resolve_ocr_lang(self.config), name="DailyMissions")
        results = ocr.detect_and_ocr(image)
        # The description of the login row also says "每日登录". A title must
        # contain the bracketed category, not merely that common substring.
        titles = [result for result in results if result.box[0] < 420
                  and re.search(r"[【\[]\s*每日\s*[】\]]", result.ocr_text)]
        titles.sort(key=lambda result: result.box[1])
        missions = []
        top, bottom = OCR_DAILY_MISSIONS.area[1], OCR_DAILY_MISSIONS.area[3]

        for title in titles:
            title_y = (title.box[1] + title.box[3]) / 2
            counter_box = (500, round(title_y + 64), 600, round(title_y + 92))
            reward_box = (918, round(title_y + 51), 962, round(title_y + 80))
            # Validate the ORIGINAL field rectangles before any crop/clamp.
            # A visible heading at the viewport edge does not make its hidden
            # fraction or reward readable. Cropped rows are read after scrolling.
            if title.box[1] <= top + 1 or max(counter_box[3], reward_box[3]) > bottom:
                logger.info(f"Daily missions: incomplete edge row {title.ocr_text!r}")
                continue

            counts = [result for result in results
                      if 495 <= (result.box[0] + result.box[2]) / 2 <= 605
                      and abs((result.box[1] + result.box[3]) / 2 - title_y - 78) <= 10
                      and _counter(result.ocr_text) is not None]
            rewards = [result for result in results
                       if 915 <= (result.box[0] + result.box[2]) / 2 <= 965
                       and abs((result.box[1] + result.box[3]) / 2 - title_y - 66) <= 10
                       and re.fullmatch(r"\s*\d+\s*", result.ocr_text)]
            if len(counts) > 1 or len(rewards) > 1:
                logger.warning(f"Daily missions: ambiguous row fields {title.ocr_text!r}")
                return None
            counter_text = counts[0].ocr_text if counts else ""
            if not counts:
                # Completed rows are dimmed: the text detector can omit a valid
                # fraction. Read its entire geometric field in grayscale, which
                # keeps the first digit visible against the green progress bar.
                field = crop(image, counter_box, copy=False)
                field = cv2.cvtColor(cv2.cvtColor(field, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)
                counter_text = ocr.ocr_single_line(field, direct_ocr=True)
            count = _counter(counter_text)
            reward_text = rewards[0].ocr_text if rewards else ocr.ocr_single_line(
                crop(image, reward_box, copy=False), direct_ocr=True)
            if count is None or re.fullmatch(r"\s*\d+\s*", reward_text) is None:
                logger.warning(f"Daily missions: unreadable row {title.ocr_text!r}, "
                               f"counter={counter_text!r}, points={reward_text!r}")
                return None
            points = int(reward_text)
            if not 0 < points <= 100:
                logger.warning(f"Daily missions: invalid reward {points}")
                return None
            current, total = count
            mission = DailyMission(_mission_key(title.ocr_text, total), current, total, points)
            if mission.key in {item.key for item in missions}:
                logger.warning(f"Daily missions: duplicate row {mission.key}")
                return None
            missions.append(mission)
            logger.info(f"Daily mission: {mission}")
            # Login is always complete and starts the completed suffix. Once
            # its own fields are verified, lower rows add no scheduling input;
            # their dimmed/cropped fields must not trigger more OCR or scrolling.
            if mission.key == "login" and current == total:
                break
        return tuple(missions) if missions else None

    @staticmethod
    def _daily_frames_equal(first, second) -> bool:
        return first is not None and second is not None and float(np.mean(
            np.abs(first.astype(np.int16) - second.astype(np.int16)))) < 0.5

    def _scan_daily_missions(self, skip_first_screenshot=True) -> tuple[DailyMission, ...] | None:
        """Collect downward until verified login, checking inferred completions.

        Pages:
            in: page_mission_reward_daily
            out: page_mission_reward_daily
        """
        if server.lang not in ("cn", "global_cn"):
            return None
        timeout = Timer(self.DAILY_SCAN_TIMEOUT_SECONDS).start()
        scroll_interval = Timer(self.DAILY_SCROLL_INTERVAL_SECONDS)
        previous = None
        before_swipe = None
        stable = 0
        stationary = 0
        unreadable = 0
        down_swipes = 0
        collected = {}

        for _ in range(self.DAILY_SCAN_MAX_FRAMES):
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()
            if timeout.reached():
                logger.warning("Daily missions: list scan timed out")
                return None

            # A popup can leave the selected tab recognizable under its dim
            # backdrop. Only an unobscured tab permits OCR or list movement;
            # otherwise let the shared popup handlers process this frame.
            if self.match_template_color(page_mission_reward_daily.check_button):
                frame = crop(self.device.image, OCR_DAILY_MISSIONS.area, copy=True)
                stable = stable + 1 if self._daily_frames_equal(previous, frame) else 1
                previous = frame
                if stable < self.DAILY_SCAN_STABLE_FRAMES or not scroll_interval.reached():
                    continue
                missions = self._read_daily_missions()
                if missions is None:
                    unreadable += 1
                    if unreadable >= 3:
                        logger.warning("Daily missions: three unreadable settled frames")
                        return None
                    continue
                unreadable = 0
                if before_swipe is not None:
                    stationary = stationary + 1 if self._daily_frames_equal(before_swipe, frame) else 0

                for mission in missions:
                    previous_mission = collected.get(mission.key)
                    if previous_mission is not None and previous_mission != mission:
                        logger.warning(f"Daily missions: conflicting row {mission.key}")
                        return None
                    collected[mission.key] = mission

                login = collected.get("login")
                if login is not None and login.current == login.total:
                    # The client sorts unfinished missions above completed login.
                    # Keep every settled observation from the downward pass;
                    # missing known keys now belong to the completed suffix, so
                    # no physical-bottom probe or reverse collection is needed.
                    # Validate their fixed fields and summed completed rewards
                    # against CURRENT activity. Otherwise a skipped unreadable
                    # row or entry at a retained middle position could be falsely
                    # promoted to complete. On any mismatch discard this scan;
                    # never fill missing progress solely from a stationary frame.
                    if any(DAILY_MISSION_SPECS.get(mission.key) != (mission.total, mission.points)
                           for mission in collected.values()):
                        logger.warning("Daily missions: unsupported mission fields at login boundary")
                        return None
                    inferred = DAILY_MISSION_KEYS.difference(collected)
                    for key in sorted(inferred):
                        total, points = DAILY_MISSION_SPECS[key]
                        collected[key] = DailyMission(key, total, total, points)
                    completed_points = sum(mission.points for mission in collected.values()
                                           if mission.current == mission.total)
                    activity = self._read_daily_points()
                    if activity is None or completed_points != activity:
                        logger.warning(f"Daily missions: login boundary activity mismatch, "
                                       f"expected={completed_points}, observed={activity}, "
                                       f"inferred={sorted(inferred)}")
                        return None
                    logger.info(f"Daily missions: completed login reached, {len(collected)} rows, "
                                f"{len(inferred)} completed rows inferred; activity={activity}")
                    return tuple(collected.values())

                if stationary >= 2 or down_swipes >= self.DAILY_SCAN_MAX_DOWN_SWIPES:
                    logger.warning("Daily missions: completed login boundary not found")
                    return None

                self.device.swipe((800, 550), (800, 470), duration=(0.3, 0.4))
                down_swipes += 1
                before_swipe = frame
                stable = 0
                previous = None
                scroll_interval.reset()
                continue

            stable = 0
            previous = None
            if self.handle_network_error():
                continue
            if self.handle_touch_to_close():
                continue

        logger.warning("Daily missions: frame budget exhausted")
        return None

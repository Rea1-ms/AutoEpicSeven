"""Read complete offers, compare a profile and verify the selected blue halo."""

from __future__ import annotations

import re
from collections import OrderedDict

from module.base.button import ClickButton
from module.base.timer import Timer
from module.base.utils import area_offset, color_similar, get_color
from module.config import server
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.automaton_tower.assets.assets_automaton_tower import (
    AUTOMATON_TOWER_CLEAR,
    AUTOMATON_TOWER_DEVICE_CONFIRM,
)
from tasks.automaton_tower.assets.assets_automaton_tower_device import (
    DEVICE_LEFT_SELECTED,
    DEVICE_MIDDLE_SELECTED,
    DEVICE_RIGHT_SELECTED,
    OCR_DEVICE_LEVEL_LEFT,
    OCR_DEVICE_LEVEL_MIDDLE,
    OCR_DEVICE_LEVEL_RIGHT,
    OCR_DEVICE_NAME_LEFT,
    OCR_DEVICE_NAME_MIDDLE,
    OCR_DEVICE_NAME_RIGHT,
)
from tasks.automaton_tower.device_record import DeviceRecord
from tasks.automaton_tower.policy import (
    Candidate,
    DecisionUnavailable,
    DeviceCatalog,
    Profile,
    choose_device,
    normalize_name,
)
from tasks.dungeon.assets.assets_dungeon_state import COMBAT_RESULT_CLEAR

NAME_BUTTONS = (OCR_DEVICE_NAME_LEFT, OCR_DEVICE_NAME_MIDDLE, OCR_DEVICE_NAME_RIGHT)
LEVEL_BUTTONS = (OCR_DEVICE_LEVEL_LEFT, OCR_DEVICE_LEVEL_MIDDLE, OCR_DEVICE_LEVEL_RIGHT)
SELECT_BUTTONS = (DEVICE_LEFT_SELECTED, DEVICE_MIDDLE_SELECTED, DEVICE_RIGHT_SELECTED)


def selection_button(slot: int):
    """Use the observed equal card spacing without inventing CN halo assets."""
    if server.lang == "cn":
        return ClickButton(
            area_offset(DEVICE_MIDDLE_SELECTED.button, ((slot - 1) * 370, 0)),
            name=SELECT_BUTTONS[slot].name,
        )
    return SELECT_BUTTONS[slot]


class DeviceReader:
    def __init__(self, lang: str, catalog: DeviceCatalog | None = None):
        self.lang = lang
        self.catalog = catalog or DeviceCatalog()
        self.cache = OrderedDict()

    def _name(self, image, button):
        x1, y1, x2, y2 = button.area
        pixels = image[y1:y2, x1:x2]
        key = (pixels.shape, pixels.tobytes())
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        results = Ocr(button, lang=self.lang).detect_and_ocr(image)
        results.sort(key=lambda result: (result.box[1], result.box[0]))
        text = "".join(result.ocr_text for result in results)
        if not results or any(result.score < 0.85 for result in results):
            raise DecisionUnavailable(f"装置名称未读全：{text}")
        info = self.catalog.resolve(text)
        if info is None:
            raise DecisionUnavailable(f"资料库未登记或名称未读全：{text}")
        # Only exact, complete name pixels are cached. Selection can dim the
        # text, so it must be a new key. Levels and the blue halo are never
        # authorized by this cache and are observed independently every frame.
        self.cache[key] = info
        if len(self.cache) > 24:
            self.cache.popitem(last=False)
        return info

    def read(self, image) -> tuple[Candidate, ...]:
        candidates, errors = [], []
        for slot, (name_button, level_button) in enumerate(
            zip(NAME_BUTTONS, LEVEL_BUTTONS)
        ):
            try:
                info = self._name(image, name_button)
                level = max_level = None
                if info.level is not None:
                    raw = Ocr(level_button, lang=self.lang).ocr_single_line(image)
                    matched = re.fullmatch(
                        r"Lv\.(\d+|Max)/(\d+)", normalize_name(raw), re.IGNORECASE
                    )
                    if matched is None:
                        raise DecisionUnavailable(
                            f"装置等级未读全：{info.name} / {raw}"
                        )
                    displayed_level, displayed_max = matched.groups()
                    max_level = int(displayed_max)
                    level = max_level if displayed_level.lower() == "max" else int(displayed_level)
                    if (level, max_level) != (info.level, info.max_level):
                        raise DecisionUnavailable(
                            f"装置名称与等级不一致：{info.name} / {raw}"
                        )
                candidates.append(Candidate(slot, info, level, max_level))
            except DecisionUnavailable as exc:
                errors.append(str(exc))
        if errors:
            raise DecisionUnavailable("；".join(errors))
        return tuple(candidates)

    @staticmethod
    def selected_slot(image) -> int | None:
        if server.lang == "cn":
            # All three cards in the real CN offer use identical geometry.
            # The supplied middle-selected frame is the only CN halo sample:
            # compare its colour at each translated position, never fabricate
            # left/right source images. Those two positive states still need
            # real screenshots; require exactly one halo before confirming.
            reference = DEVICE_MIDDLE_SELECTED
            selected = [
                slot for slot in range(3)
                if color_similar(
                    get_color(image, area_offset(reference.area, ((slot - 1) * 370, 0))),
                    reference.color,
                    threshold=30,
                )
            ]
            return selected[0] if len(selected) == 1 else None
        selected = [
            slot
            for slot, button in enumerate(SELECT_BUTTONS)
            if button.match_color(image, threshold=30)
        ]
        return selected[0] if len(selected) == 1 else None


class DeviceSelector:
    def __init__(self, task, profile: Profile, record: DeviceRecord):
        self.task, self.profile, self.record = task, profile, record
        self.reader = DeviceReader(task._ocr_lang())

    def _stop(self, reason):
        logger.critical(f"Automaton Tower: device selection needs attention: {reason}")
        debug = getattr(self.task, "device_debug", None)
        if debug is not None:
            debug.finish("needs_attention", error=reason)
        self.task.device.save_screenshot("automaton_tower_device", interval=0)
        raise RequestHumanTakeover(reason)

    def _destination(self):
        task = self.task
        if task._is_floor_page():
            return "floor_list"
        if task._is_prepare_page():
            return "prepare"
        if task.appear(AUTOMATON_TOWER_CLEAR, interval=0) or task.appear(
            COMBAT_RESULT_CLEAR, interval=0
        ):
            return "clear"
        return None

    def run(self, skip_first_screenshot=True) -> bool:
        """Select each positively observed offer and confirm its result.

        Pages:
            in: tower device dialog, including another card already selected
            out: floor list, preparation or clear result
        """
        task = self.task
        timeout = Timer(25).start()
        read_retry = Timer(1, count=0).clear()
        select_retry = Timer(2, count=0).clear()
        confirm_retry = Timer(2, count=0).clear()
        offer = choice = None
        confirm_requested = False
        bad_reads = 0
        debug = getattr(task, "device_debug", None)
        debug_started = False
        while True:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                task.device.screenshot()

            if task._is_entry_overlay():
                if timeout.reached():
                    self._stop("装置选择被入场弹窗阻挡超时")
                task._handle_tower_entry()
                continue

            dialog = task._is_device_dialog()
            if not dialog:
                destination = self._destination()
                if destination is not None:
                    if not confirm_requested:
                        self._stop("装置弹窗退出，但没有本次确认请求")
                    self.record.confirmed(destination)
                    if debug is not None:
                        debug.finish(destination, confirmed=True)
                    return True

            if timeout.reached():
                self._stop("等待目标选中或选择结果超时")

            if dialog and task.appear(AUTOMATON_TOWER_DEVICE_CONFIRM, interval=0):
                if debug is not None and not debug_started:
                    debug.begin(
                        task.device.image,
                        getattr(task, "current_floor", None),
                        "profile",
                    )
                    debug_started = True
                if not read_retry.reached():
                    continue
                try:
                    candidates = self.reader.read(task.device.image)
                except DecisionUnavailable as exc:
                    bad_reads += 1
                    logger.warning(
                        f"Automaton Tower: retry device OCR ({bad_reads}/3): {exc}"
                    )
                    read_retry.reset()
                    if bad_reads >= 3:
                        self._stop(str(exc))
                    continue
                bad_reads = 0
                # A changed complete offer after a confirm request is positive
                # evidence of the next reward round. Unknown frames, selection
                # animation and failed clicks cannot create or complete a round.
                if candidates != offer:
                    if offer is not None:
                        if confirm_requested:
                            self.record.confirmed("next_device_offer")
                        else:
                            self.record.abandon()
                        if debug is not None:
                            debug.finish(
                                "next_device_offer", confirmed=confirm_requested
                            )
                            debug.begin(
                                task.device.image,
                                getattr(task, "current_floor", None),
                                "profile",
                            )
                    try:
                        choice = choose_device(self.profile, candidates)
                    except DecisionUnavailable as exc:
                        self._stop(str(exc))
                    offer = candidates
                    confirm_requested = False
                    select_retry.clear()
                    confirm_retry.clear()
                    self.record.begin(
                        candidates, choice, getattr(task, "current_floor", None)
                    )
                    if debug is not None:
                        debug.decision(candidates, choice)
                    logger.info(
                        f"Automaton Tower: offers={[c.device.name for c in candidates]}"
                    )
                    logger.info(
                        f"Automaton Tower: select {choice.candidate.device.name}, {choice.reason}"
                    )

                # Green only means SOME card is selected. Require this frame's
                # complete offer and the target's unique halo before confirming.
                # Do not delay confirmation by the preceding selection timer.
                selected = self.reader.selected_slot(task.device.image)
                if selected == choice.candidate.slot:
                    if (
                        AUTOMATON_TOWER_DEVICE_CONFIRM.match_color(
                            task.device.image, threshold=30
                        )
                        and confirm_retry.reached()
                    ):
                        self.record.requested()
                        if debug is not None:
                            debug.requested(task.device.image)
                        task.device.click(AUTOMATON_TOWER_DEVICE_CONFIRM)
                        confirm_requested = True
                        confirm_retry.reset()
                elif select_retry.reached():
                    task.device.click(selection_button(choice.candidate.slot))
                    select_retry.reset()
                continue

            if task.ui_additional() or task.handle_network_error():
                continue

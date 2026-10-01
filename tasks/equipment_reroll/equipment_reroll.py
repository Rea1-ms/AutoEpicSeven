"""Manually started tool for the steel workshop substat selection screen."""

from dataclasses import dataclass
import re
from time import monotonic

import cv2

from module.base.button import ClickButton, match_template
from module.base.utils import crop, extract_white_letters
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.popup import PopupHandler
from tasks.equipment_reroll.assets.assets_equipment_reroll import (
    EQUIPMENT_REROLL_CHECK,
    EQUIPMENT_REROLL_CURRENT_CHECK,
    EQUIPMENT_REROLL_CURRENT_UNLOCKED,
    EQUIPMENT_REROLL_KEEP,
    EQUIPMENT_REROLL_LOCKED,
    EQUIPMENT_REROLL_REFRESH,
    EQUIPMENT_REROLL_REPLACE,
    EQUIPMENT_REROLL_REPLACE_CHECK,
    EQUIPMENT_REROLL_REPLACE_CONFIRM,
    EQUIPMENT_REROLL_UNLOCKED,
    OCR_EQUIPMENT_REROLL_COST,
    OCR_EQUIPMENT_REROLL_APPLIED,
    OCR_EQUIPMENT_REROLL_APPLIED_MAIN,
    OCR_EQUIPMENT_REROLL_CURRENT,
    OCR_EQUIPMENT_REROLL_CANDIDATE,
    OCR_EQUIPMENT_REROLL_MAIN,
    OCR_EQUIPMENT_REROLL_POINTS,
    OCR_EQUIPMENT_REROLL_REPLACE_STATS,
)
from tasks.equipment_reroll.rules import (
    REFRESH_COSTS, RefreshBudget, RerollPolicy, Snapshot, STAT_NAMES, Target, parse_points, parse_substat,
)


@dataclass
class PendingAction:
    kind: str
    before: Snapshot
    started: float
    last_click: float
    row: int = -1
    attempts: int = 1
    confirm_clicks: int = 0


class SubstatOcr(Ocr):
    white_threshold = 128
    scale = 2

    def pre_process(self, image):
        image = extract_white_letters(image, threshold=self.white_threshold)
        return cv2.resize(cv2.merge([image, image, image]), None, fx=self.scale, fy=self.scale)


class EquipmentReroll(PopupHandler):
    ACTION_TIMEOUT = 20
    STABLE_FRAMES = 2
    ROW_STEP = 47

    def _policy_from_config(self):
        targets = tuple(
            Target(
                getattr(self.config, f"EquipmentReroll_Stat{index}"),
                getattr(self.config, f"EquipmentReroll_Value{index}"),
            ) for index in range(1, 5)
        )
        return RerollPolicy(targets), RefreshBudget(
            self.config.EquipmentReroll_MaxRefresh,
            self.config.EquipmentReroll_MaxPoints,
            self.config.EquipmentReroll_ReservePoints,
        )

    def _is_ready(self):
        # The fixed green selection buttons distinguish an actionable screen
        # from the very same text underneath a modal overlay.
        dual = (
            self.appear(EQUIPMENT_REROLL_CHECK)
            and self.appear(EQUIPMENT_REROLL_REFRESH)
            and EQUIPMENT_REROLL_KEEP.match_color(self.device.image, threshold=20)
            and EQUIPMENT_REROLL_REPLACE.match_color(self.device.image, threshold=20)
        )
        return dual or (self._is_current_only() and self.appear(EQUIPMENT_REROLL_REFRESH))

    def _is_current_only(self):
        return (self.appear(EQUIPMENT_REROLL_CURRENT_CHECK)
                and EQUIPMENT_REROLL_CURRENT_CHECK.match_color(self.device.image, threshold=20))

    def _row_fields(self, asset, row_step=None):
        left, top, right, _ = asset.area
        for row in range(4):
            y = top + row * (self.ROW_STEP if row_step is None else row_step)
            yield ClickButton((left + 8, y + 3, right - 70, y + 27), name=f"{asset.name}_NAME_{row}")
            yield ClickButton((right - 50, y + 3, right - 8, y + 26), name=f"{asset.name}_VALUE_{row}")

    def _locked_rows(self, asset=EQUIPMENT_REROLL_UNLOCKED):
        area = asset.area
        result = []
        # One template is reused in four isolated row crops, not searched over
        # the entire column. The generated wrapper's search box is deliberately
        # unused here: each row has its own 2 px margin and must match exactly
        # one of the open/closed templates, otherwise its state is unknown.
        for row in range(4):
            offset = row * self.ROW_STEP
            search = (area[0] - 2, area[1] + offset - 2, area[2] + 2, area[3] + offset + 2)
            button = crop(self.device.image, search)
            locked = match_template(button, EQUIPMENT_REROLL_LOCKED.matched_button.image, similarity=0.85)
            unlocked = match_template(button, asset.matched_button.image, similarity=0.85)
            if locked == unlocked:
                raise ValueError(f"第 {row + 1} 条副属性锁定状态无法确认，匹配范围 {search}")
            result.append(locked)
        return tuple(result)

    def _read_substat_fields(self, fields, *, threshold, scale, name, labels=False):
        # Numeric crops contain only ASCII digits and an optional percent sign.
        # The bundled English recognizer handles these short strings reliably;
        # Chinese recognition is still required for the stat labels. Do not
        # lower confidence for 4% or couple model selection to a desired value.
        ocr = SubstatOcr(fields[0], lang="cn" if labels else "en", name=name)
        ocr.white_threshold, ocr.scale = threshold, scale
        images = [crop(self.device.image, field.area) for field in fields]
        results = ocr.ocr_multi_lines(images)
        if len(results) != len(fields):
            raise ValueError("副属性识别区域数量不一致")
        weak = [i for i, (_, score) in enumerate(results) if not score >= 0.8]
        if not weak:
            return results

        # Grey labels and thin digit/percent strokes lose contrast at the first
        # exposure level. Use one fixed second exposure only for weak fields.
        # It must independently clear the same confidence threshold AND agree
        # with the first text. Never choose the highest score or a desired roll:
        # disagreement between 4 and 5 must not manufacture a rare speed-5 roll.
        ocr.white_threshold = 200
        corroborated = ocr.ocr_multi_lines([images[i] for i in weak])
        if len(corroborated) != len(weak):
            raise ValueError("副属性对比度核验区域数量不一致")

        def normalized(text):
            text = re.sub(r"\s+", "", text).replace("％", "%")
            return text.rstrip(".．·。，：:") if labels else text

        for i, alternative in zip(weak, corroborated):
            original = results[i]
            logger.info(f"副属性对比度核验：{fields[i].name}，原始 {original}，核验 {alternative}")
            if not alternative[1] >= 0.8:
                raise ValueError(f"装备副属性识别置信度不足：{fields[i].name}，区域 {fields[i].area}")
            if normalized(original[0]) != normalized(alternative[0]):
                raise ValueError(f"装备副属性识别分歧：{original[0]} / {alternative[0]}，区域 {fields[i].area}")
            results[i] = alternative
        return results

    def _read_substats(self, fields, *, name_threshold=128, value_threshold=80, speed_threshold=128, scale=2):
        # OCR language is deliberately independent of the asset namespace.
        names = self._read_substat_fields(
            fields[::2], threshold=name_threshold, scale=scale, name="EquipmentRerollNames", labels=True,
        )
        values = [None] * len(names)
        # Single speed digits need softer contrast than the wider percent/flat
        # numbers. Select preprocessing from the independently recognized label,
        # not from a desired result or a retry of a low-confidence prediction.
        for is_speed, threshold in ((False, value_threshold), (True, speed_threshold)):
            indices = [i for i, (name, _) in enumerate(names) if (name.strip() == "速度") == is_speed]
            if not indices:
                continue
            batch = self._read_substat_fields(
                [fields[i * 2 + 1] for i in indices], threshold=threshold, scale=scale, name="EquipmentRerollValues",
            )
            for i, result in zip(indices, batch):
                values[i] = result
        results = [result for pair in zip(names, values) for result in pair]
        logger.info(f"装备副属性原始识别：{results}")
        if len(results) != len(fields) or any(score < 0.8 for _, score in results):
            raise ValueError("装备副属性识别置信度不足")
        texts = [text for text, _ in results]
        return tuple(parse_substat(texts[i], texts[i + 1]) for i in range(0, len(texts), 2))

    def _read_main(self, text):
        # The main row is read as one line so it also guards against impossible
        # goals that duplicate the item's main stat.
        main = re.fullmatch(r"\s*(.+?)\s*([0-9]+[%％]?)\s*", text)
        if main is None:
            raise ValueError(f"主属性无法识别：{text}")
        return parse_substat(main[1], main[2], validate_roll=False)

    def read_snapshot(self):
        current_only = self._is_current_only()
        if current_only:
            fields = list(self._row_fields(OCR_EQUIPMENT_REROLL_APPLIED))
            main_asset = OCR_EQUIPMENT_REROLL_APPLIED_MAIN
            lock_asset = EQUIPMENT_REROLL_CURRENT_UNLOCKED
        else:
            fields = [*self._row_fields(OCR_EQUIPMENT_REROLL_CURRENT),
                      *self._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE)]
            main_asset = OCR_EQUIPMENT_REROLL_MAIN
            lock_asset = EQUIPMENT_REROLL_UNLOCKED
        # The centered view uses grey labels; the comparison view uses white.
        stats = (self._read_substats(fields, name_threshold=255, value_threshold=64)
                 if current_only else self._read_substats(fields))
        resources = [OCR_EQUIPMENT_REROLL_POINTS, OCR_EQUIPMENT_REROLL_COST, main_asset]
        results = Ocr(resources[0], lang="cn", name="EquipmentRerollPoints").ocr_multi_lines(
            [crop(self.device.image, field.area) for field in resources]
        )
        if len(results) != 3 or any(score < 0.8 for _, score in results):
            raise ValueError("点数或主属性识别置信度不足")
        return Snapshot(
            current=stats[:4], candidate=stats[4:], locked=self._locked_rows(lock_asset),
            points=parse_points(results[0][0]), cost=parse_points(results[1][0]),
            main=self._read_main(results[2][0]),
        )

    def read_replacement(self):
        fields = list(self._row_fields(OCR_EQUIPMENT_REROLL_REPLACE_STATS, row_step=42))
        # The smaller modal font needs a larger scale and softer percent edges.
        # The main stat was already validated on the selection screen and cannot
        # reroll. Re-reading it in the modal only adds an unrelated failure gate.
        return self._read_substats(fields, value_threshold=64, speed_threshold=80, scale=3)

    def handle_replace_confirm(self, pending):
        if pending is None or pending.kind != "replace":
            raise RequestHumanTakeover("检测到未由本次工具发起的替换弹窗，请检查装备")
        try:
            stats = self.read_replacement()
        except ValueError as exc:
            logger.info(f"等待替换弹窗识别稳定：{exc}")
            return False
        if stats != pending.before.candidate:
            raise RequestHumanTakeover("替换弹窗的属性与已选择结果不一致，请检查装备")
        if pending.confirm_clicks >= 3 or monotonic() - pending.last_click < 2:
            return False
        if self.appear_then_click(EQUIPMENT_REROLL_REPLACE_CONFIRM, interval=2):
            pending.confirm_clicks += 1
            pending.last_click = monotonic()
            logger.info("已点击应用，等待装备属性实际替换")
            return True
        return False

    def _lock_button(self, row, snapshot):
        asset = EQUIPMENT_REROLL_CURRENT_UNLOCKED if not snapshot.candidate else EQUIPMENT_REROLL_UNLOCKED
        area = asset.area
        offset = row * self.ROW_STEP
        return ClickButton(
            (area[0] - 3, area[1] + offset - 2, area[2] + 3, area[3] + offset + 2),
            name=f"EQUIPMENT_REROLL_LOCK_{row}",
        )

    def _click(self, kind, snapshot, row=-1):
        button = self._lock_button(row, snapshot) if kind == "lock" else {
            "replace": EQUIPMENT_REROLL_REPLACE,
            "refresh": EQUIPMENT_REROLL_REFRESH,
        }[kind]
        action_name = {"lock": "调整锁定", "replace": "替换", "refresh": "刷新"}[kind]
        logger.info(f"装备刷新操作：{action_name}，当前点数 {snapshot.points}，价格 {snapshot.cost}")
        self.device.click(button)
        return PendingAction(kind, snapshot, monotonic(), monotonic(), row=row)

    def _confirm_pending(self, pending, snapshot, budget):
        before = pending.before
        if snapshot.main != before.main:
            raise ValueError("操作期间主属性发生变化")
        if pending.kind == "refresh":
            return budget.confirm_refresh(before, snapshot)
        if snapshot.points != before.points:
            raise ValueError("替换或锁定期间点数意外变化")
        if pending.kind == "replace":
            return snapshot.current == before.candidate
        expected = list(before.locked)
        expected[pending.row] = not expected[pending.row]
        if snapshot.current != before.current:
            raise ValueError("锁定期间当前装备属性发生变化")
        return snapshot.locked == tuple(expected)

    def execute(self, policy, budget, skip_first_screenshot=True):
        """Reroll within the configured budget, leaving the best current roll visible.

        Pages:
            in: steel workshop substat selection screen
            out: the same substat selection screen
        """
        pending = None
        previous = None
        stable = 0
        last_valid = monotonic()
        next_action_at = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            snapshot = None
            if self._is_ready():
                try:
                    snapshot = self.read_snapshot()
                except ValueError as exc:
                    logger.info(f"等待副属性画面稳定：{exc}")
            stable = stable + 1 if snapshot is not None and snapshot == previous else 1
            previous = snapshot
            ready = snapshot is not None and stable >= self.STABLE_FRAMES

            if pending is not None and monotonic() - pending.started >= self.ACTION_TIMEOUT:
                raise RequestHumanTakeover("装备刷新操作未确认，请检查当前结果；不会重复扣费刷新")
            if monotonic() - last_valid >= self.ACTION_TIMEOUT:
                raise RequestHumanTakeover("请停留在钢铁工坊的副属性选择页，并检查识别日志")

            if ready:
                last_valid = monotonic()
                if any(target.kind == snapshot.main.kind for target in policy.targets):
                    raise RequestHumanTakeover("目标副属性与主属性重复，请调整目标")
                if pending is not None:
                    try:
                        confirmed = self._confirm_pending(pending, snapshot, budget)
                    except ValueError as exc:
                        raise RequestHumanTakeover(str(exc)) from exc
                    if confirmed:
                        if pending.kind == "refresh":
                            # This screen intentionally stays open across many
                            # paid rerolls. Clear framework histories only after
                            # exact payment and preserved locks prove progress;
                            # clearing on a click would hide an actual stuck UI.
                            self.device.click_record_clear()
                            self.device.stuck_record_clear()
                        logger.info("装备操作已确认：" + {"lock": "调整锁定", "replace": "替换",
                                                       "refresh": "刷新"}[pending.kind])
                        pending = None
                    else:
                        # A changed candidate alone does not prove a paid refresh
                        # finished. Wait for the exact point deduction, including
                        # identical rerolls and delayed balances. Never re-click
                        # a paid refresh whose outcome is still unknown.
                        if (pending.kind != "refresh" and snapshot == pending.before
                                and pending.attempts < 3
                                and monotonic() - pending.last_click >= 2):
                            button = (self._lock_button(pending.row, pending.before) if pending.kind == "lock"
                                      else EQUIPMENT_REROLL_REPLACE)
                            self.device.click(button)
                            pending.attempts += 1
                            pending.last_click = monotonic()
                            next_action_at = pending.last_click + 2
                            previous, stable = None, 0
                        continue

                # Positive terminal states are checked before any new click.
                if policy.complete(snapshot.current):
                    logger.info("四条副属性均已达标")
                    return "四条副属性均已达标"

                # Accept an improvement even on the last paid roll. Comparing
                # before the budget exit prevents throwing away a speed-5 roll
                # merely because its refresh exhausted the available points.
                replace = policy.should_replace(snapshot)
                desired = policy.desired_locks(snapshot.current)
                stop_reason = budget.stop_reason(snapshot, cost=REFRESH_COSTS[sum(desired)])
                if stop_reason and not replace:
                    logger.info(stop_reason)
                    return stop_reason

                if monotonic() < next_action_at:
                    continue
                if replace:
                    pending = self._click("replace", snapshot)
                    next_action_at = pending.last_click + 2
                    previous, stable = None, 0
                    continue
                # Unlock stale/user-selected rows first, then lock the achieved
                # priorities. This avoids briefly requesting a third lock.
                changes = [row for row in range(4) if snapshot.locked[row] and not desired[row]]
                changes += [row for row in range(4) if desired[row] and not snapshot.locked[row]]
                if changes:
                    pending = self._click("lock", snapshot, changes[0])
                    next_action_at = pending.last_click + 2
                    previous, stable = None, 0
                    continue
                pending = self._click("refresh", snapshot)
                next_action_at = pending.last_click + 2
                previous, stable = None, 0
                continue

            if self.appear(EQUIPMENT_REROLL_REPLACE_CHECK):
                if self.handle_replace_confirm(pending):
                    next_action_at = pending.last_click + 2
                    previous, stable = None, 0
                continue
            if self.handle_network_error():
                previous, stable = None, 0
                continue

    def run(self):
        lang = self.config.Emulator_GameLanguage
        if lang not in (None, "", "auto", "cn"):
            raise RequestHumanTakeover("装备副属性刷新目前仅支持游戏简体中文")
        try:
            policy, budget = self._policy_from_config()
        except ValueError as exc:
            raise RequestHumanTakeover(str(exc)) from exc
        logger.hr("装备副属性刷新", level=1)
        logger.info("目标优先级：" + " > ".join(f"{STAT_NAMES[t.kind]} {t.value}" for t in policy.targets))
        reason = self.execute(policy, budget)
        logger.info(f"装备刷新结束：{reason}，已刷新 {budget.refreshes} 次，消耗 {budget.spent} 点")
        return True

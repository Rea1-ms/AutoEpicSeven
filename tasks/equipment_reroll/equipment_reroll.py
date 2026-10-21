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
    OCR_EQUIPMENT_REROLL_APPLIED,
    OCR_EQUIPMENT_REROLL_CURRENT,
    OCR_EQUIPMENT_REROLL_CANDIDATE,
    OCR_EQUIPMENT_REROLL_POINTS,
    OCR_EQUIPMENT_REROLL_REPLACE_STATS,
)
from tasks.equipment_reroll.rules import (
    REFRESH_COSTS, RefreshBudget, RejectedCandidate, RerollPolicy, Snapshot, STAT_LABELS,
    Substat, Target, UnreadSubstat, parse_points, parse_substat,
)
from tasks.equipment_reroll.recognition import (
    GoldMarkerDetector, PointBalanceEstimate, RecognitionCache, RefreshCostTemplates,
    StatNameTemplates, candidate_signature, point_signature,
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
        targets = []
        for index in range(1, 5):
            kind = getattr(self.config, f"EquipmentReroll_Stat{index}")
            value = getattr(self.config, f"EquipmentReroll_Value{index}")
            try:
                targets.append(Target(kind, value))
            except ValueError as exc:
                raise ValueError(f"Invalid target {index} ({kind} {value}): {exc}") from exc
        return RerollPolicy(tuple(targets)), RefreshBudget(
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
                raise ValueError(f"Substat lock state could not be confirmed at row {row + 1}, search area {search}")
            result.append(locked)
        return tuple(result)

    def _read_substat_fields(self, fields, *, threshold, scale, name, labels=False):
        # Numeric crops contain only ASCII digits and an optional percent sign.
        # The bundled English recognizer handles these short strings reliably;
        # Chinese recognition is still required for the stat labels. Do not
        # lower confidence for 4% or couple model selection to a desired value.
        if not hasattr(self, "_field_cache"):
            self._field_cache = RecognitionCache()
            self._name_templates = StatNameTemplates()
        images = [crop(self.device.image, field.area) for field in fields]
        keys = [(labels, threshold, scale, image.shape, image.tobytes()) for image in images]
        results = [self._field_cache.get(key) for key in keys]
        if labels:
            for i, image in enumerate(images):
                if results[i] is None:
                    results[i] = self._name_templates.match(image, scale)
        missing = [i for i, result in enumerate(results) if result is None]
        if not missing:
            return results
        ocr = SubstatOcr(fields[0], lang="cn" if labels else "en", name=name)
        ocr.white_threshold, ocr.scale = threshold, scale
        recognized = ocr.ocr_multi_lines([images[i] for i in missing])
        if len(recognized) != len(missing):
            raise ValueError("Substat OCR result count does not match the requested regions")
        for i, result in zip(missing, recognized):
            results[i] = result
        weak = [i for i in missing if not results[i][1] >= 0.8]

        # Grey labels and thin digit/percent strokes lose contrast at the first
        # exposure level. Use one fixed second exposure only for weak fields.
        # It must independently clear the same confidence threshold AND agree
        # with the first text. Never choose the highest score or a desired roll:
        # disagreement between 4 and 5 must not manufacture a rare speed-5 roll.
        ocr.white_threshold = 200
        corroborated = ocr.ocr_multi_lines([images[i] for i in weak]) if weak else []
        if len(corroborated) != len(weak):
            raise ValueError("Substat contrast verification result count does not match the requested regions")

        def normalized(text):
            text = re.sub(r"\s+", "", text).replace("％", "%")
            return text.rstrip(".．·。，：:") if labels else text

        for i, alternative in zip(weak, corroborated):
            original = results[i]
            logger.info(f"EquipmentReroll: contrast verification for {fields[i].name}, original {original}, check {alternative}")
            if not alternative[1] >= 0.8:
                raise ValueError(f"Substat OCR confidence is too low: {fields[i].name}, area {fields[i].area}")
            if normalized(original[0]) != normalized(alternative[0]):
                raise ValueError(f"Substat OCR readings disagree: {original[0]} / {alternative[0]}, area {fields[i].area}")
            results[i] = alternative
        # Cache only independently validated fields. Numeric entries require
        # byte-identical crops, including the percent sign. A changed digit or
        # layout always invalidates the entry; no cached number proves payment.
        for i in missing:
            if labels:
                results[i] = normalized(results[i][0]), results[i][1]
                self._name_templates.remember(images[i], scale, results[i][0])
            self._field_cache.put(keys[i], results[i])
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
        if not hasattr(self, "_logged_stats"):
            self._logged_stats = {}
        key = fields[0].name
        if self._logged_stats.get(key) != results:
            logger.info(f"EquipmentReroll: raw substat OCR results: {results}")
            self._logged_stats[key] = results
        if len(results) != len(fields) or any(score < 0.8 for _, score in results):
            raise ValueError("Substat OCR confidence is too low")
        texts = [text for text, _ in results]
        return tuple(parse_substat(texts[i], texts[i + 1]) for i in range(0, len(texts), 2))

    def _read_candidate(self, fields, current, locked, policy):
        if not hasattr(self, "_gold_markers"):
            self._gold_markers = GoldMarkerDetector()
        # The player permits reusing protected rows after startup confirms
        # their names, values and lock order. Left-side lock state is still
        # observed each frame; grey candidate icons/glyphs no longer need OCR,
        # template matching or stability checks. Replacement clears the current
        # record until the newly applied equipment has been read and confirmed.
        reuse_locked = getattr(self, "_recorded_current", None) == current
        protected = tuple(i for i, value in enumerate(locked) if value and reuse_locked)
        gold = self._gold_markers.rows(self.device.image, self.ROW_STEP, skip_rows=protected)
        signature = candidate_signature(self.device.image, fields, gold, skip_rows=protected)
        rows = [current[i] if i in protected else UnreadSubstat() for i in range(4)]
        names = [STAT_LABELS[row.kind] if isinstance(row, Substat) else None for row in rows]

        def read_names(indices):
            needed = [i for i in indices if names[i] is None]
            if not needed:
                return
            labels = self._read_substat_fields(
                [fields[i * 2] for i in needed], threshold=128, scale=2,
                name="EquipmentRerollNames", labels=True,
            )
            for i, (name, _) in zip(needed, labels):
                names[i] = name
                rows[i] = UnreadSubstat(name)

        def read_rows(indices):
            indices = list(indices)
            read_names(indices)
            for is_speed, threshold in ((False, 80), (True, 128)):
                needed = [i for i in indices if isinstance(rows[i], UnreadSubstat)
                          and (names[i] == "速度") == is_speed]
                if not needed:
                    continue
                values = self._read_substat_fields(
                    [fields[i * 2 + 1] for i in needed], threshold=threshold, scale=2,
                    name="EquipmentRerollValues",
                )
                for i, (text, _) in zip(needed, values):
                    rows[i] = parse_substat(names[i], text)

        # Before the first confirmed current record, read protected candidate
        # rows too. Startup must prove both columns agree before trusting them.
        read_rows([i for i, value in enumerate(locked) if value and i not in protected])
        current_values = {stat.kind: stat.value for stat in current}
        target = next((t for t in policy.targets if current_values.get(t.kind, 0) < t.value), None)
        gold_rows = [i for i, full in enumerate(gold) if full]
        if target is not None and gold_rows:
            read_names(gold_rows)
            # Attack/defense/health can each occur twice (flat and percent).
            # Read matching gold rows; the number must still prove '%' and the
            # actual roll. In particular, a real speed-4 screenshot is gold too.
            read_rows([i for i in gold_rows if names[i] == STAT_LABELS[target.kind]])
            values = {row.kind: row.value for row in rows if isinstance(row, Substat)}
            candidate_value = min(values.get(target.kind, 0), target.value)
            current_value = min(current_values.get(target.kind, 0), target.value)
            if candidate_value > current_value:
                # A possible replacement always becomes a complete observation
                # before any click, modal comparison or post-apply validation.
                read_rows(range(4))
                return tuple(rows)
        if signature != getattr(self, "_last_rejected_signature", None):
            reason = "no gold markers" if not gold_rows else "gold substats do not improve the current target"
            read_count = sum(isinstance(row, Substat) for row in rows) - len(protected)
            logger.info(f"EquipmentReroll: {reason}, candidate values read: {read_count}, "
                        f"protected rows reused: {len(protected)}")
            self._last_rejected_signature = signature
        return RejectedCandidate(tuple(rows), policy.targets, signature)

    def _read_points(self, *, fresh=False):
        if not hasattr(self, "_resource_cache"):
            self._resource_cache = RecognitionCache(limit=8)
        image = crop(self.device.image, OCR_EQUIPMENT_REROLL_POINTS.area)
        key = (image.shape, image.tobytes())
        result = None if fresh else self._resource_cache.get(key)
        if result is not None:
            return result
        recognized = Ocr(OCR_EQUIPMENT_REROLL_POINTS, lang="cn", name="EquipmentRerollPoints").ocr_multi_lines([image])
        if len(recognized) != 1 or not recognized[0][1] >= 0.8:
            raise ValueError("Point balance OCR confidence is too low")
        result = parse_points(recognized[0][0])
        self._resource_cache.put(key, result)
        return result

    def _read_resources(self, locked):
        cost = RefreshCostTemplates().read(self.device.image, locked)
        signature = point_signature(self.device.image)
        estimate = getattr(self, "_point_estimate", None)
        points, signature = estimate.observe(signature) if estimate is not None else (None, signature)
        self._last_points_signature = signature
        return self._read_points() if points is None else points, cost

    def _audit_points(self):
        estimate = self._point_estimate
        if estimate.points is None or not estimate.unchecked_refreshes:
            return
        try:
            points = self._read_points(fresh=True)
            estimate.verify(points)
        except ValueError as exc:
            logger.critical(f"EquipmentReroll: {exc}. Please check the workshop point balance before restarting.")
            raise RequestHumanTakeover from exc
        logger.info(f"EquipmentReroll: point balance audit confirmed: {points}")

    def read_snapshot(self):
        current_only = self._is_current_only()
        if current_only:
            fields = list(self._row_fields(OCR_EQUIPMENT_REROLL_APPLIED))
            lock_asset = EQUIPMENT_REROLL_CURRENT_UNLOCKED
        else:
            fields = list(self._row_fields(OCR_EQUIPMENT_REROLL_CURRENT))
            lock_asset = EQUIPMENT_REROLL_UNLOCKED
        stats = getattr(self, "_recorded_current", None)
        if stats is None:
            # The centered view uses grey labels; the comparison view uses
            # white. Read only while initializing or confirming a replacement.
            stats = (self._read_substats(fields, name_threshold=255, value_threshold=64)
                     if current_only else self._read_substats(fields))
        locked = self._locked_rows(lock_asset)
        candidate = ()
        if not current_only:
            candidate_fields = list(self._row_fields(OCR_EQUIPMENT_REROLL_CANDIDATE))
            policy = getattr(self, "_recognition_policy", None)
            candidate = (self._read_candidate(candidate_fields, stats, locked, policy)
                         if policy is not None else self._read_substats(candidate_fields))
        points, cost = self._read_resources(locked)
        return Snapshot(
            current=stats, candidate=candidate, locked=locked, points=points, cost=cost,
            points_signature=self._last_points_signature,
        )

    def read_replacement(self):
        fields = list(self._row_fields(OCR_EQUIPMENT_REROLL_REPLACE_STATS, row_step=42))
        # The smaller modal font needs a larger scale and softer percent edges.
        # The player chooses the fixed main stat before entering this tool.
        # Re-reading it cannot help the substat decision and adds a failure gate.
        return self._read_substats(fields, value_threshold=64, speed_threshold=80, scale=3)

    def handle_replace_confirm(self, pending):
        if pending is None or pending.kind != "replace":
            logger.critical(
                "EquipmentReroll: replacement dialog was not initiated by this tool. "
                "Please check the equipment before restarting."
            )
            raise RequestHumanTakeover
        try:
            stats = self.read_replacement()
        except ValueError as exc:
            logger.info(f"EquipmentReroll: waiting for stable replacement dialog readings: {exc}")
            return False
        if stats != pending.before.candidate:
            logger.critical(
                "EquipmentReroll: replacement dialog substats do not match the selected candidate. "
                "Please check the equipment before restarting."
            )
            raise RequestHumanTakeover
        if pending.confirm_clicks >= 3 or monotonic() - pending.last_click < 2:
            return False
        if self.appear_then_click(EQUIPMENT_REROLL_REPLACE_CONFIRM, interval=2):
            pending.confirm_clicks += 1
            pending.last_click = monotonic()
            logger.info("EquipmentReroll: apply clicked, waiting for current equipment substats to update")
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
        logger.info(f"EquipmentReroll: action {kind}, points {snapshot.points}, refresh cost {snapshot.cost}")
        self.device.click(button)
        estimate = getattr(self, "_point_estimate", None)
        if kind == "refresh" and estimate is not None and estimate.points is not None:
            estimate.pending_cost = snapshot.cost
        if kind == "replace":
            # Only replacement can change current substats in this manually
            # selected workshop. Invalidate before waiting for its result; the
            # candidate must never become current merely because we clicked.
            self._recorded_current = None
        return PendingAction(kind, snapshot, monotonic(), monotonic(), row=row)

    def _confirm_pending(self, pending, snapshot, budget):
        before = pending.before
        if pending.kind == "refresh":
            return budget.confirm_refresh(before, snapshot)
        if snapshot.points != before.points:
            raise ValueError("Point balance changed unexpectedly during replacement or lock adjustment")
        if pending.kind == "replace":
            return snapshot.current == before.candidate
        expected = list(before.locked)
        expected[pending.row] = not expected[pending.row]
        if snapshot.current != before.current:
            raise ValueError("Current equipment substats changed during lock adjustment")
        return snapshot.locked == tuple(expected)

    def execute(self, policy, budget, skip_first_screenshot=True):
        """Reroll within the configured budget, leaving the best current roll visible.

        Pages:
            in: steel workshop substat selection screen
            out: the same substat selection screen
        """
        pending = None
        self._recorded_current = None
        self._recognition_policy = policy
        # Recognition data belongs to this invocation only. Reusing the tool
        # object for another equipment must not reuse its learned templates.
        self._field_cache = RecognitionCache()
        self._name_templates = StatNameTemplates()
        self._gold_markers = GoldMarkerDetector()
        self._resource_cache = RecognitionCache(limit=8)
        self._point_estimate = PointBalanceEstimate()
        self._last_points_signature = b""
        self._last_rejected_signature = None
        self._logged_stats = {}
        previous = None
        stable = 0
        initial_locks_checked = False
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
                    logger.info(f"EquipmentReroll: waiting for stable substat readings: {exc}")
            stable = stable + 1 if snapshot is not None and snapshot == previous else 1
            previous = snapshot
            ready = snapshot is not None and stable >= self.STABLE_FRAMES

            if pending is not None and monotonic() - pending.started >= self.ACTION_TIMEOUT:
                logger.critical(
                    f"EquipmentReroll: {pending.kind} action could not be confirmed. "
                    "Please check the current result; paid refresh will not be repeated."
                )
                raise RequestHumanTakeover
            if monotonic() - last_valid >= self.ACTION_TIMEOUT:
                logger.critical(
                    "EquipmentReroll: no stable workshop substat selection screen was recognized. "
                    "Please stay on the substat selection screen and check the recognition logs."
                )
                raise RequestHumanTakeover

            if ready:
                last_valid = monotonic()
                if not initial_locks_checked:
                    try:
                        policy.validate_initial_locks(snapshot)
                        if snapshot.candidate:
                            for row, locked in enumerate(snapshot.locked):
                                if locked and snapshot.candidate[row] != snapshot.current[row]:
                                    raise ValueError(f"Initial locked candidate does not match current substat at row {row + 1}")
                    except ValueError as exc:
                        logger.critical(f"EquipmentReroll: {exc}. Please adjust the initial locks or target order.")
                        raise RequestHumanTakeover from exc
                    initial_locks_checked = True
                if pending is not None:
                    try:
                        confirmed = self._confirm_pending(pending, snapshot, budget)
                        if (confirmed and pending.kind == "refresh"
                                and self._point_estimate.points is not None):
                            self._point_estimate.confirm_refresh(snapshot.points, snapshot.points_signature)
                    except ValueError as exc:
                        logger.critical(f"EquipmentReroll: {exc}. Please check the equipment and point balance.")
                        raise RequestHumanTakeover from exc
                    if confirmed:
                        if pending.kind == "refresh":
                            if self._point_estimate.unchecked_refreshes >= PointBalanceEstimate.CHECK_EVERY:
                                self._audit_points()
                            # This screen intentionally stays open across many
                            # paid rerolls. Clear framework histories only after
                            # changed balance glyphs and preserved locks prove progress;
                            # clearing on a click would hide an actual stuck UI.
                            self.device.click_record_clear()
                            self.device.stuck_record_clear()
                        logger.info(f"EquipmentReroll: {pending.kind} action confirmed")
                        pending = None
                    else:
                        # A changed candidate alone does not prove a paid refresh
                        # finished. Wait for stable changed balance glyphs, including
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

                # Freeze only a stable, confirmed current roll. Paid refreshes
                # and lock toggles cannot reroll this column, so lighting or
                # animation changes must not trigger another full OCR. During
                # replacement the record stays empty until actual current
                # stats equal the selected candidate on two stable frames.
                if self._recorded_current is None:
                    self._recorded_current = snapshot.current
                    logger.info("EquipmentReroll: current equipment substats recorded: " + ", ".join(
                        f"{stat.kind} {stat.value}" for stat in snapshot.current
                    ))
                if self._point_estimate.points is None and snapshot.points_signature:
                    self._point_estimate.initialize(snapshot.points, snapshot.points_signature)

                # Positive terminal states are checked before any new click.
                if policy.complete(snapshot.current):
                    self._audit_points()
                    logger.info("EquipmentReroll: all four substat targets reached")
                    return "All four substat targets reached"

                # Accept an improvement even on the last paid roll. Comparing
                # before the budget exit prevents throwing away a speed-5 roll
                # merely because its refresh exhausted the available points.
                replace = policy.should_replace(snapshot)
                desired = policy.desired_locks(snapshot.current)
                stop_reason = budget.stop_reason(snapshot, cost=REFRESH_COSTS[sum(desired)])
                if stop_reason and not replace:
                    self._audit_points()
                    logger.info(f"EquipmentReroll: {stop_reason}")
                    return stop_reason

                if monotonic() < next_action_at:
                    continue
                if replace:
                    pending = self._click("replace", snapshot)
                    next_action_at = pending.last_click + 2
                    previous, stable = None, 0
                    continue
                # Startup rejects incompatible user locks. After a confirmed
                # replacement, remove any obsolete locks before adding new
                # priorities, so an intermediate state never needs three locks.
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
        logger.hr("Equipment substat reroll", level=1)
        lang = self.config.Emulator_GameLanguage
        if lang not in (None, "", "auto", "cn"):
            logger.critical(
                f"EquipmentReroll: only Simplified Chinese is supported, configured language: {lang}. "
                "Please select Simplified Chinese before starting this tool."
            )
            raise RequestHumanTakeover
        try:
            policy, budget = self._policy_from_config()
        except ValueError as exc:
            # The framework prints only 'Request human takeover' and discards
            # this exception's message. Log the rejected input here before any
            # OCR or paid action; never silently raise a user-entered goal from
            # speed 4 to 5, because that changes their intended spend and stop.
            logger.critical(f"EquipmentReroll: invalid configuration: {exc}. Please correct the tool settings.")
            raise RequestHumanTakeover from exc
        logger.info("EquipmentReroll: target priority: " + " > ".join(f"{t.kind} {t.value}" for t in policy.targets))
        # Each takeover site logs its own reason before raising. Do not log it
        # again here: the bare framework exception deliberately carries no text.
        reason = self.execute(policy, budget)
        logger.info(f"EquipmentReroll: finished: {reason}, refreshes {budget.refreshes}, points spent {budget.spent}")
        return True

"""
Epic Seven expiring mail collection task.

Flow:
    main -> mail -> sort by remaining time ascending -> claim mails within threshold -> main

Pages:
    in: page_main, page_mail
    out: page_main
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from module.base.timer import Timer
from module.exception import ScriptError
from module.logger import logger
from module.ocr.ocr import OcrWhiteLetterOnComplexBackground
from tasks.base.page import page_mail, page_main
from tasks.base.ui import UI
from tasks.gacha.assets.assets_gacha import (
    SUMMON_FREE_CONTINUE,
    SUMMON_NEW,
    SUMMON_NEXT_PAGE,
    SUMMON_RESULT_BACK,
    SUMMON_SKIP,
)
from tasks.mail.assets.assets_mail import (
    GOTO_LINK,
    OCR_REMAINING_TIME,
    RECEIVE,
    RECEIVE_CONFIRM,
    REMAINING_TIME_ASCENDING,
    REMAINING_TIME_DESCENDING,
    REMAINING_TIME_UNSELECTED,
    SORTING_CRITERIA,
)


@dataclass
class MailRemainingState:
    raw_text: str
    normalized_text: str
    unit: str | None = None
    value: int | None = None
    valid: bool = False
    unlimited: bool = False

    def __str__(self) -> str:
        if self.unlimited:
            return f"{self.raw_text} (unlimited)"
        if not self.valid:
            return f"{self.raw_text} (invalid)"
        return f"{self.raw_text} ({self.value}{self.unit})"


class OcrMailRemainingTime(OcrWhiteLetterOnComplexBackground):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace(" ", "")
        result = result.replace("剩余", "")
        result = result.replace("剩", "")
        result = result.replace("O", "0").replace("o", "0")
        result = result.replace("I", "1").replace("l", "1")
        result = result.replace("小時", "小时")
        result = result.replace("分鐘", "分钟")
        result = re.sub(r"(\d{1,3})时(?!间|辰)", r"\1小时", result)
        result = re.sub(r"(\d{1,3})分(?!钟)", r"\1分钟", result)
        result = re.sub(r"(\d{1,3})\s*days?", r"\1d", result, flags=re.IGNORECASE)
        result = re.sub(r"(\d{1,3})\s*hours?", r"\1h", result, flags=re.IGNORECASE)
        result = re.sub(r"(\d{1,3})\s*minutes?", r"\1m", result, flags=re.IGNORECASE)
        result = re.sub(r"(\d{1,3})\s*seconds?", r"\1s", result, flags=re.IGNORECASE)
        return result


class Mail(UI):
    SORT_TIMEOUT_SECONDS = 20
    RECEIVE_FLOW_TIMEOUT_SECONDS = 45
    RECEIVE_DONE_CONFIRM_SECONDS = 1.5
    CLICK_INTERVAL_SECONDS = 1
    NEXT_DAY_CHECK_DELAY = timedelta(hours=23, minutes=55)
    SAME_DAY_COARSE_CHECK = timedelta(hours=12)
    SAME_DAY_MEDIUM_CHECK = timedelta(hours=6)
    SAME_DAY_SHORT_CHECK = timedelta(hours=3)
    SAME_DAY_FINE_CHECK = timedelta(hours=1)
    SAME_DAY_LAST_MILE_CHECK = timedelta(minutes=20)
    SAME_LABEL_RECORD_VALIDITY = timedelta(days=3)
    SAME_LABEL_FINE_BOUNDARY = timedelta(hours=18)
    SAME_LABEL_SHORT_BOUNDARY = timedelta(hours=12)
    SAME_LABEL_MEDIUM_BOUNDARY = timedelta(hours=6)

    def _ocr_lang(self) -> str:
        lang = getattr(self.config, "Emulator_GameLanguage", "cn")
        if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
            return "cn"
        if lang in ("en", "global_en", "en_us"):
            return "en"
        if lang in ("jp", "ja", "ja_jp"):
            return "jp"
        if lang in ("tw", "zh_tw"):
            return "tw"
        return "cn"

    def _mail_threshold(self) -> str:
        return getattr(self.config, "Mail_CollectWithin", "1h")

    @staticmethod
    def _mail_has_link_asset() -> bool:
        return any(True for _ in GOTO_LINK.iter_buttons())

    def _record_remaining_observation(self, normalized_text: str, now: datetime | None = None) -> None:
        now = (now or datetime.now()).replace(microsecond=0)
        last_text = getattr(self.config, "Mail_LastRemainingText", "")
        last_since = getattr(self.config, "Mail_LastRemainingSince", None)

        if (
            normalized_text
            and normalized_text == last_text
            and isinstance(last_since, datetime)
            and now >= last_since
            and now - last_since <= self.SAME_LABEL_RECORD_VALIDITY
        ):
            same_since = last_since.replace(microsecond=0)
        else:
            same_since = now

        with self.config.multi_set():
            self.config.Mail_LastCheckAt = now
            self.config.Mail_LastRemainingText = normalized_text
            self.config.Mail_LastRemainingSince = same_since

    def _clear_remaining_observation(self) -> None:
        now = datetime.now().replace(microsecond=0)
        with self.config.multi_set():
            self.config.Mail_LastCheckAt = now
            self.config.Mail_LastRemainingText = ""
            self.config.Mail_LastRemainingSince = now

    def _same_label_age(self, normalized_text: str, now: datetime | None = None) -> timedelta | None:
        now = (now or datetime.now()).replace(microsecond=0)
        last_text = getattr(self.config, "Mail_LastRemainingText", "")
        last_since = getattr(self.config, "Mail_LastRemainingSince", None)

        if normalized_text != last_text:
            return None
        if not isinstance(last_since, datetime):
            return None
        if last_since > now:
            return None
        age = now - last_since
        if age > self.SAME_LABEL_RECORD_VALIDITY:
            return None
        return age

    def _enter_mail(self, skip_first_screenshot=True) -> bool:
        logger.info("Mail: enter mailbox")
        self.ui_goto(page_mail, skip_first_screenshot=skip_first_screenshot)
        return True

    def _receive_available(self, interval=0) -> bool:
        return self.appear(RECEIVE, interval=interval)

    def _ensure_remaining_time_ascending(self, skip_first_screenshot=True) -> bool:
        logger.info("Mail: ensure sorting is remaining time ascending")
        timeout = Timer(self.SORT_TIMEOUT_SECONDS, count=60).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Mail: sorting setup timeout")
                return False

            if self.appear(REMAINING_TIME_ASCENDING):
                logger.info("Mail: sorting already set to remaining time ascending")
                self.device.click(SORTING_CRITERIA)
                return True

            if self.appear(REMAINING_TIME_DESCENDING):
                logger.info("Mail: switch remaining time from descending to ascending")
                self.device.click(REMAINING_TIME_DESCENDING)
                timeout.reset()
                continue

            if self.appear(REMAINING_TIME_UNSELECTED):
                logger.info("Mail: switch sorting key to remaining time")
                self.device.click(REMAINING_TIME_UNSELECTED)
                timeout.reset()
                continue

            if self.appear_then_click(SORTING_CRITERIA, interval=self.CLICK_INTERVAL_SECONDS):
                timeout.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def _ocr_top_remaining_text(self) -> str:
        text = OcrMailRemainingTime(
            OCR_REMAINING_TIME,
            lang=self._ocr_lang(),
            name="MailRemainingTimeOCR",
        ).ocr_single_line(self.device.image)
        return text

    def _parse_remaining_text(self, text: str) -> MailRemainingState:
        normalized = text.replace(" ", "")
        normalized = normalized.replace("剩余", "")
        normalized = normalized.replace("剩", "")

        if not normalized:
            return MailRemainingState(raw_text=text, normalized_text=normalized)

        if "无限制" in normalized:
            return MailRemainingState(
                raw_text=text,
                normalized_text="无限制",
                valid=True,
                unlimited=True,
            )

        patterns = (
            (r"(?P<value>\d{1,3})天", "d"),
            (r"(?P<value>\d{1,3})(?:小时|h)", "h"),
            (r"(?P<value>\d{1,3})(?:分钟|m)", "m"),
            (r"(?P<value>\d{1,3})(?:秒|s)", "s"),
        )
        for pattern, unit in patterns:
            matched = re.search(pattern, normalized, flags=re.IGNORECASE)
            if matched:
                value = int(matched.group("value"))
                return MailRemainingState(
                    raw_text=text,
                    normalized_text=normalized,
                    unit=unit,
                    value=value,
                    valid=True,
                )

        return MailRemainingState(raw_text=text, normalized_text=normalized)

    def _ocr_top_remaining_state(self) -> MailRemainingState:
        text = self._ocr_top_remaining_text()
        state = self._parse_remaining_text(text)
        logger.attr("MailRemainingText", state.raw_text)
        if state.valid and not state.unlimited:
            logger.attr("MailRemainingParsed", f"{state.value}{state.unit}")
        elif state.unlimited:
            logger.info("Mail: top mail has unlimited remaining time")
        else:
            logger.warning(f"Mail: failed to parse remaining time: {state.raw_text}")
        return state

    def _is_within_threshold(self, state: MailRemainingState) -> bool:
        if not state.valid or state.unlimited:
            return False

        threshold = self._mail_threshold()
        if threshold == "1d":
            if state.unit == "d":
                return state.value <= 1
            return state.unit in ("h", "m", "s")

        if threshold == "1h":
            if state.unit == "h":
                return state.value <= 1
            return state.unit in ("m", "s")

        raise ScriptError(f"Unknown mail threshold: {threshold}")

    def _predict_next_check_target(self, state: MailRemainingState) -> datetime | None:
        if not state.valid or state.unlimited:
            return None

        now = datetime.now().replace(microsecond=0)
        threshold = self._mail_threshold()

        def cap_target(target: datetime) -> datetime:
            return min(target.replace(microsecond=0), now + self.NEXT_DAY_CHECK_DELAY)

        if threshold == "1d":
            if state.unit == "d" and state.value > 1:
                return cap_target(now + timedelta(days=state.value - 1))
            return None

        if threshold == "1h":
            if state.unit == "h" and state.value > 1:
                return cap_target(now + timedelta(hours=state.value - 1))

            if state.unit == "d":
                if state.value > 1:
                    return cap_target(now + self.NEXT_DAY_CHECK_DELAY)

                label_age = self._same_label_age(state.normalized_text, now=now)
                if label_age is None:
                    return cap_target(now + self.SAME_DAY_COARSE_CHECK)
                if label_age < self.SAME_LABEL_MEDIUM_BOUNDARY:
                    return cap_target(now + self.SAME_DAY_MEDIUM_CHECK)
                if label_age < self.SAME_LABEL_SHORT_BOUNDARY:
                    return cap_target(now + self.SAME_DAY_SHORT_CHECK)
                if label_age < self.SAME_LABEL_FINE_BOUNDARY:
                    return cap_target(now + self.SAME_DAY_FINE_CHECK)
                return cap_target(now + self.SAME_DAY_LAST_MILE_CHECK)

            return None

        raise ScriptError(f"Unknown mail threshold: {threshold}")

    def _schedule_after_no_receive(self) -> None:
        logger.info("Mail: no RECEIVE button, wait for next server update")
        self._clear_remaining_observation()
        self.config.task_delay(server_update=True)

    def _schedule_after_ineligible(self, state: MailRemainingState) -> None:
        now = datetime.now().replace(microsecond=0)
        target = self._predict_next_check_target(state)
        self._record_remaining_observation(state.normalized_text, now=now)

        if target is None:
            logger.info("Mail: remaining time not collectible yet, fallback to next server update")
            self.config.task_delay(server_update=True)
            return

        logger.info(
            f"Mail: top mail not within threshold {self._mail_threshold()}, "
            f"delay to {target} from {state.raw_text}"
        )
        self.config.task_delay(target=target)

    def _android_back(self) -> None:
        logger.info("Mail: send Android back keyevent")
        self.device.adb_shell(["input", "keyevent", "4"])

    def _handle_mail_reward_flow(self, skip_first_screenshot=True) -> bool:
        logger.info("Mail: handle receive reward flow")
        timeout = Timer(self.RECEIVE_FLOW_TIMEOUT_SECONDS, count=120).start()
        done_confirm = Timer(self.RECEIVE_DONE_CONFIRM_SECONDS, count=4).start()
        pending_android_back = False
        android_back_timer = Timer(1.2, count=3)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Mail: receive reward flow timeout")
                return False

            if pending_android_back and android_back_timer.reached():
                self._android_back()
                pending_android_back = False
                timeout.reset()
                done_confirm.reset()
                continue

            if self._mail_has_link_asset() and self.appear_then_click(GOTO_LINK, interval=self.CLICK_INTERVAL_SECONDS):
                logger.info("Mail: open preview link before receive confirm")
                pending_android_back = True
                android_back_timer.reset()
                timeout.reset()
                done_confirm.reset()
                continue

            if self.appear_then_click(RECEIVE_CONFIRM, interval=self.CLICK_INTERVAL_SECONDS):
                logger.info("Mail: confirm receive popup")
                timeout.reset()
                done_confirm.reset()
                continue

            if self.appear(SUMMON_NEW, interval=self.CLICK_INTERVAL_SECONDS):
                logger.info("Mail: close summon new overlay")
                self.device.click(SUMMON_NEW)
                timeout.reset()
                done_confirm.reset()
                continue

            if self.appear_then_click(SUMMON_SKIP, interval=self.CLICK_INTERVAL_SECONDS):
                logger.info("Mail: skip summon animation")
                timeout.reset()
                done_confirm.reset()
                continue

            if SUMMON_NEXT_PAGE is not None and self.appear_then_click(
                SUMMON_NEXT_PAGE, interval=self.CLICK_INTERVAL_SECONDS
            ):
                logger.info("Mail: continue summon result pages")
                timeout.reset()
                done_confirm.reset()
                continue

            summon_back = self.appear(SUMMON_RESULT_BACK)
            summon_continue = self.appear(SUMMON_FREE_CONTINUE)
            if summon_back and summon_continue:
                logger.info("Mail: continue summon reward result")
                self.device.click(SUMMON_FREE_CONTINUE)
                timeout.reset()
                done_confirm.reset()
                continue
            if summon_back:
                logger.info("Mail: return from summon reward result")
                self.device.click(SUMMON_RESULT_BACK)
                timeout.reset()
                done_confirm.reset()
                continue

            if self.handle_touch_to_close(interval=0.5):
                timeout.reset()
                done_confirm.reset()
                continue

            if self.ui_additional():
                timeout.reset()
                done_confirm.reset()
                continue

            if self.handle_network_error():
                timeout.reset()
                done_confirm.reset()
                continue

            if self.ui_page_appear(page_mail) and done_confirm.reached():
                logger.info("Mail: receive reward flow settled")
                return True

    def _claim_top_mail(self, skip_first_screenshot=True) -> bool:
        logger.info("Mail: claim top mail")
        claimed = False
        timeout = Timer(8, count=24).start()

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Mail: claim top mail click timeout")
                return claimed

            if not self._receive_available(interval=self.CLICK_INTERVAL_SECONDS):
                if claimed:
                    return True

            if self._receive_available(interval=self.CLICK_INTERVAL_SECONDS):
                self.device.click(RECEIVE)
                claimed = True
                self.interval_reset(RECEIVE, interval=self.CLICK_INTERVAL_SECONDS)
                return self._handle_mail_reward_flow(skip_first_screenshot=True)

            if self.ui_additional():
                timeout.reset()
                continue
            if self.handle_network_error():
                timeout.reset()
                continue

    def run(self) -> bool:
        logger.hr("Mail", level=1)

        if not self.device.app_is_running():
            from tasks.login.login import Login

            Login(self.config, device=self.device).app_start()

        if not self._mail_has_link_asset():
            logger.warning(
                "Mail: GOTO_LINK asset is unavailable for current asset language. "
                "Preview-mail branch may require adding this asset."
            )

        self._enter_mail(skip_first_screenshot=False)

        if not self._receive_available(interval=0):
            self.ui_goto(page_main, skip_first_screenshot=True)
            self._schedule_after_no_receive()
            return True

        if not self._ensure_remaining_time_ascending(skip_first_screenshot=True):
            self.ui_goto(page_main, skip_first_screenshot=True)
            self.config.task_delay(success=False)
            return False

        while 1:
            self.device.screenshot()

            if not self._receive_available(interval=0):
                self.ui_goto(page_main, skip_first_screenshot=True)
                self._schedule_after_no_receive()
                return True

            state = self._ocr_top_remaining_state()
            if not state.valid or state.unlimited:
                self.ui_goto(page_main, skip_first_screenshot=True)
                self._schedule_after_ineligible(state)
                return True

            if not self._is_within_threshold(state):
                self.ui_goto(page_main, skip_first_screenshot=True)
                self._schedule_after_ineligible(state)
                return True

            self._clear_remaining_observation()
            if not self._claim_top_mail(skip_first_screenshot=True):
                self.ui_goto(page_main, skip_first_screenshot=True)
                self.config.task_delay(success=False)
                return False

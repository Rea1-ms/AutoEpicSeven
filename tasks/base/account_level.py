import re
from dataclasses import dataclass
from typing import Literal

from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.base.assets.assets_base_main_page import OCR_ACCOUNT_LEVEL


@dataclass(frozen=True)
class AccountLevel:
    kind: Literal["rank", "srank"]
    value: int

    @property
    def milestone_unlocked(self) -> bool:
        return self.kind == "srank" or self.value >= 70


def parse_account_level(text: str) -> AccountLevel | None:
    text = re.sub(r"\s+", "", text).replace("。", ".").replace("．", ".")
    matched = re.fullmatch(r"(S\.?)?Rank\.?(\d{1,2})", text, flags=re.IGNORECASE)
    if matched is None:
        return None
    kind = "srank" if matched[1] else "rank"
    value = int(matched[2])
    if not 1 <= value <= (20 if kind == "srank" else 70):
        return None
    return AccountLevel(kind, value)


class AccountLevelMixin:
    account_level: AccountLevel | None = None

    def read_main_account_level(self) -> AccountLevel | None:
        """Read the current main-page rank without navigating or taking a screenshot.

        Pages:
            in: page_main, without an overlay
            out: unchanged
        """
        # This is a current-frame hint, never an account-global coordinate cache.
        # Clear it on every attempt, including non-main pages and invalid OCR, so
        # a login/account change cannot silently reuse the previous layout hint.
        self.account_level = None
        if not self.is_in_main():
            return None
        lang = self.config.Emulator_GameLanguage
        if lang not in ("cn", "en", "jp", "tw"):
            lang = "cn"
        text = Ocr(OCR_ACCOUNT_LEVEL, lang=lang, name="AccountLevel").ocr_single_line(self.device.image)
        self.account_level = parse_account_level(text)
        if self.account_level is None:
            logger.info("Account level unavailable; try all route entry layouts")
        else:
            logger.attr("AccountLevelParsed", f"{self.account_level.kind}:{self.account_level.value}")
        return self.account_level

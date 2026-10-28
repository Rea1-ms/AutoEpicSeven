import re
from dataclasses import dataclass

from module.base.button import ClickButton
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import OcrWhiteLetterOnComplexBackground
from tasks.dungeon.assets.assets_dungeon_action import CHOOSE_TEAM
from tasks.dungeon.assets.assets_dungeon_configs_episode_entry import (
    CHAPTER_SWITCH_CHECK,
    EPISODE_CHOOSE,
    EPISODE_STAGE_SEARCH,
    LAST_CHAPTER,
    NEXT_CHAPTER,
    OCR_CHAPTER_NAME,
    OCR_CURRENT_STAGE,
    READY_TO_FIGHT,
)
from tasks.dungeon.assets.assets_dungeon_configs_episode_materials_check_catalyst import (
    CATALYST_EPIC_BENEVOLENT,
    CATALYST_EPIC_FIGHTING_SPIRIT,
    CATALYST_EPIC_MYSTERIOUS,
    CATALYST_EPIC_OATH,
    CATALYST_EPIC_SECRET,
    CATALYST_EPIC_SNIPER,
    CATALYST_RARE_BENEVOLENT,
    CATALYST_RARE_FIGHTING_SPIRIT,
    CATALYST_RARE_MYSTERIOUS,
    CATALYST_RARE_OATH,
    CATALYST_RARE_SECRET,
    CATALYST_RARE_SNIPER,
)
from tasks.dungeon.assets.assets_dungeon_configs_episode_materials_check_character_growth import (
    BREATH_OF_KARMA,
    FROZEN_SEED,
    HEART_OF_THE_WOODS,
    TRACES_OF_BRILLIANCE,
)
from tasks.dungeon.assets.assets_dungeon_configs_episode_materials_index_catalyst import (
    CATALYST_EPIC_BENEVOLENT_INDEX,
    CATALYST_EPIC_FIGHTING_SPIRIT_INDEX,
    CATALYST_EPIC_MYSTERIOUS_INDEX,
    CATALYST_EPIC_OAT_INDEX,
    CATALYST_EPIC_SECRET_INDEX,
    CATALYST_EPIC_SNIPER_INDEX,
    CATALYST_RARE_BENEVOLENT_INDEX,
    CATALYST_RARE_FIGHTING_SPIRIT_INDEX,
    CATALYST_RARE_MYSTERIOUS_INDEX,
    CATALYST_RARE_OATH_INDEX,
    CATALYST_RARE_SECRET_INDEX,
    CATALYST_RARE_SNIPER_INDEX,
)
from tasks.dungeon.assets.assets_dungeon_configs_episode_materials_index_character_growth import (
    BREATH_OF_KARMA_INDEX,
    FROZEN_SEED_INDEX,
    HEART_OF_THE_WOODS_INDEX,
    TRACES_OF_BRILLIANCE_INDEX,
)
from tasks.dungeon.assets.assets_dungeon_configs_side_story_entry import SUPPORTER_CHECK
from tasks.dungeon.episode_materials import (
    EPISODE_MATERIAL_STAGES,
    material_chapter_order,
    parse_material_chapter,
)
from tasks.dungeon.episode_navigation import EPISODE_ENTRIES, EpisodeSelectionMixin


class OcrEpisodeLabel(OcrWhiteLetterOnComplexBackground):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("：", ":").replace("．", ".")
        result = result.replace("|", "1").replace("I", "1").replace("l", "1")
        return result.strip()


@dataclass(frozen=True)
class EpisodeMaterialPlan:
    key: str
    chapter: int | str
    stage: int
    material_check: object
    material_index: object

    @property
    def stage_label(self) -> str:
        return f"{self.chapter}-{self.stage}"


MATERIAL_ASSETS = {
    "CATALYST_RARE_BENEVOLENT": (CATALYST_RARE_BENEVOLENT, CATALYST_RARE_BENEVOLENT_INDEX),
    "CATALYST_RARE_SECRET": (CATALYST_RARE_SECRET, CATALYST_RARE_SECRET_INDEX),
    "CATALYST_RARE_FIGHTING_SPIRIT": (CATALYST_RARE_FIGHTING_SPIRIT, CATALYST_RARE_FIGHTING_SPIRIT_INDEX),
    "CATALYST_RARE_SNIPER": (CATALYST_RARE_SNIPER, CATALYST_RARE_SNIPER_INDEX),
    "HEART_OF_THE_WOODS": (HEART_OF_THE_WOODS, HEART_OF_THE_WOODS_INDEX),
    "CATALYST_RARE_OATH": (CATALYST_RARE_OATH, CATALYST_RARE_OATH_INDEX),
    "CATALYST_RARE_MYSTERIOUS": (CATALYST_RARE_MYSTERIOUS, CATALYST_RARE_MYSTERIOUS_INDEX),
    "CATALYST_EPIC_OATH": (CATALYST_EPIC_OATH, CATALYST_EPIC_OAT_INDEX),
    "BREATH_OF_KARMA": (BREATH_OF_KARMA, BREATH_OF_KARMA_INDEX),
    "CATALYST_EPIC_SNIPER": (CATALYST_EPIC_SNIPER, CATALYST_EPIC_SNIPER_INDEX),
    "CATALYST_EPIC_FIGHTING_SPIRIT": (CATALYST_EPIC_FIGHTING_SPIRIT, CATALYST_EPIC_FIGHTING_SPIRIT_INDEX),
    "FROZEN_SEED": (FROZEN_SEED, FROZEN_SEED_INDEX),
    "CATALYST_EPIC_BENEVOLENT": (CATALYST_EPIC_BENEVOLENT, CATALYST_EPIC_BENEVOLENT_INDEX),
    "CATALYST_EPIC_MYSTERIOUS": (CATALYST_EPIC_MYSTERIOUS, CATALYST_EPIC_MYSTERIOUS_INDEX),
    "CATALYST_EPIC_SECRET": (CATALYST_EPIC_SECRET, CATALYST_EPIC_SECRET_INDEX),
    "TRACES_OF_BRILLIANCE": (TRACES_OF_BRILLIANCE, TRACES_OF_BRILLIANCE_INDEX),
}

EPISODE_MATERIAL_PLANS = {
    episode: {
        key: EpisodeMaterialPlan(key, chapter, stage, *MATERIAL_ASSETS[key])
        for key, (chapter, stage) in destinations.items()
    }
    for episode, destinations in EPISODE_MATERIAL_STAGES.items()
}
# Preserve existing imports and saved configurations while expanding the task.
EPISODE4_MATERIAL_PLANS = EPISODE_MATERIAL_PLANS[4]


class EpisodeNavigateMixin(EpisodeSelectionMixin):
    EPISODE_NAVIGATE_TIMEOUT_SECONDS = 45
    EPISODE_CHAPTER_SETTLE_SECONDS = 1.2
    EPISODE_STAGE_SETTLE_SECONDS = 1.0

    def _combat_episode_material_key(self) -> str:
        return getattr(self.config, "Combat_Episode4Material", "BREATH_OF_KARMA")

    def _combat_episode_number(self) -> int:
        value = getattr(self.config, "Combat_EpisodeNumber", 4)
        if value not in (1, 2, 3, 4, 5, "1", "2", "3", "4", "5") or isinstance(value, bool):
            logger.critical(f"Episode: unsupported material episode {value!r}; select Episode 1-5")
            raise RequestHumanTakeover
        return int(value)

    def _combat_episode_target(self) -> EpisodeMaterialPlan:
        episode = self._combat_episode_number()
        key = self._combat_episode_material_key()
        plan = EPISODE_MATERIAL_PLANS[episode].get(key)
        if plan is None:
            logger.critical(
                f"Episode {episode}: no confirmed destination for {key}; "
                "select a supported episode/material combination"
            )
            raise RequestHumanTakeover
        return plan

    def _is_episode_stage_page(self) -> bool:
        return self.match_template_luma(CHAPTER_SWITCH_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_episode_choose_page(self) -> bool:
        # `EPISODE_CHOOSE` is a page marker, not an action button.
        # The direct episode route (`MAIN_GOTO_EPISODE` / `MENU_GOTO_EPISODE`)
        # already lands on the stage-selection hub. Clicking this asset again
        # would be a wrong transition and has caused route regressions before.
        return self.match_template_luma(EPISODE_CHOOSE, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _is_episode_supporter_page(self) -> bool:
        return self.match_template_luma(SUPPORTER_CHECK, similarity=self.COMBAT_CHECK_SIMILARITY)

    def _extract_episode_number(self, text: str) -> int:
        matched = re.search(r"(\d{1,2})", text)
        if matched is None:
            return 0
        return int(matched.group(1))

    def _ocr_episode_chapter_text(self) -> str:
        return OcrEpisodeLabel(
            OCR_CHAPTER_NAME,
            lang=self._ocr_lang(),
            name="EpisodeChapterName",
        ).ocr_single_line(self.device.image)

    def _ocr_episode_chapter_index(self) -> int | str:
        text = self._ocr_episode_chapter_text()
        index = parse_material_chapter(text)
        logger.attr("EpisodeChapterIndex", index)
        return index

    def _ocr_episode_current_stage_text(self) -> str:
        return OcrEpisodeLabel(
            OCR_CURRENT_STAGE,
            lang=self._ocr_lang(),
            name="EpisodeCurrentStage",
        ).ocr_single_line(self.device.image)

    def _ocr_episode_current_stage_index(self) -> int:
        text = self._ocr_episode_current_stage_text()
        index = self._extract_episode_number(text)
        logger.attr("EpisodeCurrentStageIndex", index)
        return index

    def _episode_has_target_material(self, plan: EpisodeMaterialPlan) -> bool:
        # Only check whether the target material appears in the right-side drop preview.
        # Do not include color checks here: we don't care whether the button is enabled/disabled.
        # Using match_template_color() here has caused false negatives due to background/brightness variance,
        # which then made the state machine scroll the list away from an already-selected target stage.
        return self.match_template_luma(
            plan.material_check,
            similarity=self.COMBAT_CHECK_SIMILARITY,
        )

    def _swipe_episode_stage_list(self, toward_top=False, strong=False, *, area=None) -> None:
        # Material farming keeps its existing locator area. Story progression
        # supplies its full text-list area because later episodes use another
        # viewport; both flows derive gestures from assets rather than pixels.
        left, area_top, right, area_bottom = area if area is not None else EPISODE_STAGE_SEARCH.area
        x = (left + right) // 2
        if strong:
            top = area_top + 5
            bottom = area_bottom - 5
        else:
            top = area_top + 20
            bottom = area_bottom - 20
        if toward_top:
            start = (x, top)
            end = (x, bottom)
        else:
            start = (x, bottom)
            end = (x, top)
        self.device.swipe(start, end, duration=(0.2, 0.3))

    def _click_episode_stage_list_lower_third(self) -> None:
        left, top, right, bottom = EPISODE_STAGE_SEARCH.area
        height = bottom - top
        lower_top = top + (height * 2) // 3
        click_button = ClickButton(
            area=(left, lower_top, right, bottom),
            name="EPISODE_STAGE_SEARCH_LOWER_THIRD",
        )
        self.device.click(click_button)

    def _navigate_episode4(self, skip_first_screenshot=True) -> bool:
        """
        Navigate to the configured episode material prepare page.

        Pages:
            in: main or combat pages
            out: prepare page
        """
        target = self._combat_episode_target()
        target_episode = self._combat_episode_number()
        logger.hr(f"Episode {target_episode} Navigate", level=2)
        logger.info(f"Episode: navigate to {target_episode}-{target.stage_label} for {target.key}")

        # A missing selector control or an ignored chapter click must remain
        # bounded even with slow screenshots. During chapter switching, reset
        # only when a new chapter is observed below; repeatedly seeing/clicking
        # the old chapter must not extend this navigation deadline.
        timeout = Timer(self.EPISODE_NAVIGATE_TIMEOUT_SECONDS, count=0).start()
        chapter_settle = Timer(self.EPISODE_CHAPTER_SETTLE_SECONDS, count=0).clear()
        stage_settle = Timer(self.EPISODE_STAGE_SETTLE_SECONDS, count=0).clear()
        last_chapter = None
        pending_chapter_entry = None
        # Episode OCR is a page identity read, not a per-frame progress
        # signal. Keep one successful value while the map remains open; a
        # selector/card click invalidates it before the next page is observed.
        # Re-reading the same stable title only adds latency and used to flood
        # the log during the chapter-list scan.
        episode_number = None
        # Chapter identity survives stage-list clicks. Only a chapter arrow
        # or selector transition can change it; invalidate before observing
        # that transition so an ignored click still retries the old chapter.
        chapter_index = None

        # `material_index` is the left-list locator asset.
        # Expand its search area to the whole stage list and use it as the
        # primary navigator. `OCR_CURRENT_STAGE` stays as a read-only observer.
        target.material_index.load_search(EPISODE_STAGE_SEARCH.area)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self._is_prepare_page():
                logger.info("Episode: reached prepare page")
                return True

            if timeout.reached():
                logger.warning("Episode: navigate timeout")
                return False

            if self._is_episode_selector():
                episode_number = None
                chapter_index = None
                chapter_settle.clear()
                stage_settle.clear()
                if self._selector_episode() != target_episode:
                    entry, _ = EPISODE_ENTRIES[target_episode]
                    if self.appear(entry):
                        self._click_episode_navigation(entry)
                else:
                    entries = self._chapter_entries()
                    if pending_chapter_entry is not None:
                        entries = [entry for entry in entries if entry.area == pending_chapter_entry]
                    if entries and self._click_episode_navigation(entries[0]):
                        # A click is not an arrival. Retry this same visible
                        # card until a map is positively observed; never use
                        # saved coordinates after its star disappears.
                        pending_chapter_entry = entries[0].area
                        episode_number = None
                continue

            if self._is_episode_supporter_page() and self.appear_then_click(CHOOSE_TEAM, interval=1):
                logger.info("Episode: choose supporter team")
                timeout.reset()
                continue

            if self._is_episode_choose_page() or self._is_episode_stage_page():
                if episode_number is None:
                    episode_number = self._ocr_episode_number()
                episode = episode_number
                if episode is None:
                    continue
                if episode != target_episode:
                    if self._handle_episode_switch():
                        episode_number = None
                        chapter_index = None
                    continue
                pending_chapter_entry = None
                if chapter_settle.started() and not chapter_settle.reached():
                    continue
                if stage_settle.started() and not stage_settle.reached():
                    continue

                if not chapter_index:
                    chapter_index = self._ocr_episode_chapter_index()
                current_chapter = chapter_index
                if not current_chapter:
                    continue
                if current_chapter != last_chapter:
                    last_chapter = current_chapter
                    timeout.reset()
                if current_chapter != target.chapter:
                    # Read the suffix again after each arrow click. A click from 8 to
                    # 8S must not let the old Chapter 8 frame select material;
                    # special chapters sort after their numbered parent.
                    forward = material_chapter_order(current_chapter) < material_chapter_order(target.chapter)
                    button = NEXT_CHAPTER if forward else LAST_CHAPTER
                    if self.appear_then_click(button, interval=1):
                        logger.info(
                            f"Episode: switch chapter {current_chapter} -> {target.chapter}"
                        )
                        chapter_settle.reset()
                        chapter_index = None
                        stage_settle.clear()
                    continue

                current_stage = self._ocr_episode_current_stage_index()

                # Keep this logic in a fixed order with only four branches.
                # A previous regression mixed in old "reset after clicking target" behavior:
                # even after jumping to the target stage, the next loop could fail to see the index
                # briefly and scroll the list back away.
                #
                # Contract:
                # 1) Target material is visible in the right-side drop preview: current stage is the target.
                #    Only handle READY_TO_FIGHT; do not touch the left list.
                # 2) OCR reports the current stage number is above the target: swipe hard to the top.
                # 3) Target index is visible in the left list search area: click it to jump to the stage.
                # 4) Otherwise the target is further down: click the lower third to advance.
                if self._episode_has_target_material(target):
                    if self.appear_then_click(READY_TO_FIGHT, interval=2):
                        logger.info(f"Episode: enter fight for {target.stage_label}")
                    else:
                        logger.info(
                            f"Episode: target stage {target.stage_label} selected, wait for ready button"
                        )
                    timeout.reset()
                    continue

                if current_stage > 0 and current_stage > target.stage:
                    logger.info(
                        "Episode: target is above current stage, reset list to top "
                        f"(current={current_stage}, target={target.stage})"
                    )
                    self._swipe_episode_stage_list(toward_top=True, strong=True)
                    stage_settle.reset()
                    timeout.reset()
                    continue

                if self.appear_then_click(target.material_index, interval=1):
                    logger.info(f"Episode: click target material index for {target.stage_label}")
                    stage_settle.reset()
                    timeout.reset()
                    continue

                logger.info("Episode: target material index not visible, click lower third to advance")
                self._click_episode_stage_list_lower_third()
                stage_settle.reset()
                timeout.reset()
                continue

            if self._handle_dungeon_additional():
                timeout.reset()
                continue

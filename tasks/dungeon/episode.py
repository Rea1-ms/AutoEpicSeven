import re
from dataclasses import dataclass

from module.base.button import ClickButton
from module.base.timer import Timer
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

from tasks.dungeon.assets.assets_dungeon_configs_episode_entry import READY_TO_FIGHT




class OcrEpisodeLabel(OcrWhiteLetterOnComplexBackground):
    def after_process(self, result):
        result = super().after_process(result)
        result = result.replace("：", ":").replace("．", ".")
        result = result.replace("|", "1").replace("I", "1").replace("l", "1")
        return result.strip()


@dataclass(frozen=True)
class EpisodeMaterialPlan:
    key: str
    chapter: int
    stage: int
    material_check: object
    material_index: object

    @property
    def stage_label(self) -> str:
        return f"{self.chapter}-{self.stage}"


EPISODE4_MATERIAL_PLANS = {
    "CATALYST_RARE_BENEVOLENT": EpisodeMaterialPlan(
        key="CATALYST_RARE_BENEVOLENT",
        chapter=1,
        stage=9,
        material_check=CATALYST_RARE_BENEVOLENT,
        material_index=CATALYST_RARE_BENEVOLENT_INDEX,
    ),
    "CATALYST_RARE_SECRET": EpisodeMaterialPlan(
        key="CATALYST_RARE_SECRET",
        chapter=2,
        stage=10,
        material_check=CATALYST_RARE_SECRET,
        material_index=CATALYST_RARE_SECRET_INDEX,
    ),
    "CATALYST_RARE_FIGHTING_SPIRIT": EpisodeMaterialPlan(
        key="CATALYST_RARE_FIGHTING_SPIRIT",
        chapter=3,
        stage=10,
        material_check=CATALYST_RARE_FIGHTING_SPIRIT,
        material_index=CATALYST_RARE_FIGHTING_SPIRIT_INDEX,
    ),
    "CATALYST_RARE_SNIPER": EpisodeMaterialPlan(
        key="CATALYST_RARE_SNIPER",
        chapter=4,
        stage=8,
        material_check=CATALYST_RARE_SNIPER,
        material_index=CATALYST_RARE_SNIPER_INDEX,
    ),
    "HEART_OF_THE_WOODS": EpisodeMaterialPlan(
        key="HEART_OF_THE_WOODS",
        chapter=4,
        stage=10,
        material_check=HEART_OF_THE_WOODS,
        material_index=HEART_OF_THE_WOODS_INDEX,
    ),
    "CATALYST_RARE_OATH": EpisodeMaterialPlan(
        key="CATALYST_RARE_OATH",
        chapter=5,
        stage=5,
        material_check=CATALYST_RARE_OATH,
        material_index=CATALYST_RARE_OATH_INDEX,
    ),
    "CATALYST_RARE_MYSTERIOUS": EpisodeMaterialPlan(
        key="CATALYST_RARE_MYSTERIOUS",
        chapter=5,
        stage=10,
        material_check=CATALYST_RARE_MYSTERIOUS,
        material_index=CATALYST_RARE_MYSTERIOUS_INDEX,
    ),
    "CATALYST_EPIC_OATH": EpisodeMaterialPlan(
        key="CATALYST_EPIC_OATH",
        chapter=6,
        stage=8,
        material_check=CATALYST_EPIC_OATH,
        material_index=CATALYST_EPIC_OAT_INDEX,
    ),
    "BREATH_OF_KARMA": EpisodeMaterialPlan(
        key="BREATH_OF_KARMA",
        chapter=6,
        stage=10,
        material_check=BREATH_OF_KARMA,
        material_index=BREATH_OF_KARMA_INDEX,
    ),
    "CATALYST_EPIC_SNIPER": EpisodeMaterialPlan(
        key="CATALYST_EPIC_SNIPER",
        chapter=7,
        stage=10,
        material_check=CATALYST_EPIC_SNIPER,
        material_index=CATALYST_EPIC_SNIPER_INDEX,
    ),
    "CATALYST_EPIC_FIGHTING_SPIRIT": EpisodeMaterialPlan(
        key="CATALYST_EPIC_FIGHTING_SPIRIT",
        chapter=8,
        stage=7,
        material_check=CATALYST_EPIC_FIGHTING_SPIRIT,
        material_index=CATALYST_EPIC_FIGHTING_SPIRIT_INDEX,
    ),
    "FROZEN_SEED": EpisodeMaterialPlan(
        key="FROZEN_SEED",
        chapter=8,
        stage=9,
        material_check=FROZEN_SEED,
        material_index=FROZEN_SEED_INDEX,
    ),
    "CATALYST_EPIC_BENEVOLENT": EpisodeMaterialPlan(
        key="CATALYST_EPIC_BENEVOLENT",
        chapter=9,
        stage=7,
        material_check=CATALYST_EPIC_BENEVOLENT,
        material_index=CATALYST_EPIC_BENEVOLENT_INDEX,
    ),
    "CATALYST_EPIC_MYSTERIOUS": EpisodeMaterialPlan(
        key="CATALYST_EPIC_MYSTERIOUS",
        chapter=9,
        stage=10,
        material_check=CATALYST_EPIC_MYSTERIOUS,
        material_index=CATALYST_EPIC_MYSTERIOUS_INDEX,
    ),
    "CATALYST_EPIC_SECRET": EpisodeMaterialPlan(
        key="CATALYST_EPIC_SECRET",
        chapter=10,
        stage=6,
        material_check=CATALYST_EPIC_SECRET,
        material_index=CATALYST_EPIC_SECRET_INDEX,
    ),
    "TRACES_OF_BRILLIANCE": EpisodeMaterialPlan(
        key="TRACES_OF_BRILLIANCE",
        chapter=10,
        stage=9,
        material_check=TRACES_OF_BRILLIANCE,
        material_index=TRACES_OF_BRILLIANCE_INDEX,
    ),
}


class EpisodeNavigateMixin:
    EPISODE_NAVIGATE_TIMEOUT_SECONDS = 45
    EPISODE_CHAPTER_SETTLE_SECONDS = 1.2
    EPISODE_STAGE_SETTLE_SECONDS = 1.0

    def _combat_episode_material_key(self) -> str:
        return getattr(self.config, "Combat_Episode4Material", "BREATH_OF_KARMA")

    def _combat_episode_target(self) -> EpisodeMaterialPlan:
        key = self._combat_episode_material_key()
        plan = EPISODE4_MATERIAL_PLANS.get(key)
        if plan is None:
            logger.warning(f"Episode4: unknown material key={key}, fallback to BREATH_OF_KARMA")
            plan = EPISODE4_MATERIAL_PLANS["BREATH_OF_KARMA"]
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

    def _ocr_episode_chapter_index(self) -> int:
        text = self._ocr_episode_chapter_text()
        index = self._extract_episode_number(text)
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

    def _swipe_episode_stage_list(self, toward_top=False, strong=False) -> None:
        x = (EPISODE_STAGE_SEARCH.area[0] + EPISODE_STAGE_SEARCH.area[2]) // 2
        if strong:
            top = EPISODE_STAGE_SEARCH.area[1] + 5
            bottom = EPISODE_STAGE_SEARCH.area[3] - 5
        else:
            top = EPISODE_STAGE_SEARCH.area[1] + 20
            bottom = EPISODE_STAGE_SEARCH.area[3] - 20
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
        Navigate from main/combat pages to the Episode 4 prepare page.

        Pages:
            in: main or combat pages
            out: prepare page
        """
        target = self._combat_episode_target()
        logger.hr("Episode 4 Navigate", level=2)
        logger.info(f"Episode4: navigate to {target.stage_label} for {target.key}")

        timeout = Timer(self.EPISODE_NAVIGATE_TIMEOUT_SECONDS, count=160).start()
        chapter_settle = Timer(self.EPISODE_CHAPTER_SETTLE_SECONDS, count=0).clear()
        stage_settle = Timer(self.EPISODE_STAGE_SETTLE_SECONDS, count=0).clear()

        # `material_index` is the left-list locator asset.
        # Expand its search area to the whole stage list and use it as the
        # primary navigator. `OCR_CURRENT_STAGE` stays as a read-only observer.
        target.material_index.load_search(EPISODE_STAGE_SEARCH.area)

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning("Episode4: navigate timeout")
                return False

            if self._is_prepare_page():
                logger.info("Episode4: reached prepare page")
                return True

            if self._is_episode_supporter_page():
                if self.appear_then_click(CHOOSE_TEAM, interval=1):
                    logger.info("Episode4: choose supporter team")
                    timeout.reset()
                    continue

            if self._is_episode_choose_page() or self._is_episode_stage_page():
                if chapter_settle.started() and not chapter_settle.reached():
                    continue
                if stage_settle.started() and not stage_settle.reached():
                    continue

                current_chapter = self._ocr_episode_chapter_index()
                if current_chapter > 0 and current_chapter != target.chapter:
                    button = NEXT_CHAPTER if current_chapter < target.chapter else LAST_CHAPTER
                    if self.appear_then_click(button, interval=1):
                        logger.info(
                            f"Episode4: switch chapter {current_chapter} -> {target.chapter}"
                        )
                        chapter_settle.reset()
                        stage_settle.clear()
                    timeout.reset()
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
                        logger.info(f"Episode4: enter fight for {target.stage_label}")
                    else:
                        logger.info(
                            f"Episode4: target stage {target.stage_label} selected, wait for ready button"
                        )
                    timeout.reset()
                    continue

                if current_stage > 0 and current_stage > target.stage:
                    logger.info(
                        "Episode4: target is above current stage, reset list to top "
                        f"(current={current_stage}, target={target.stage})"
                    )
                    self._swipe_episode_stage_list(toward_top=True, strong=True)
                    stage_settle.reset()
                    timeout.reset()
                    continue

                if self.appear_then_click(target.material_index, interval=1):
                    logger.info(f"Episode4: click target material index for {target.stage_label}")
                    stage_settle.reset()
                    timeout.reset()
                    continue

                logger.info("Episode4: target material index not visible, click lower third to advance")
                self._click_episode_stage_list_lower_third()
                stage_settle.reset()
                timeout.reset()
                continue

            if self._handle_dungeon_additional():
                timeout.reset()
                continue

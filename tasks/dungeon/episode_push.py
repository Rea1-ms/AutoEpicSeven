"""Manually started main-story progression, independent of material farming."""

import re
from dataclasses import dataclass

from module.base.button import ClickButton
from module.base.timer import Timer
from module.config import server
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.language import resolve_ocr_lang
from module.ocr.ocr import Ocr
from tasks.base.page import page_episode
from tasks.base.ui import UI
from tasks.dungeon.assets.assets_dungeon_action import (
    AUTO_COMBAT,
    CHOOSE_TEAM,
    COMBAT_RESULT_CONFIRM,
    COMBAT_START,
)
from tasks.dungeon.assets.assets_dungeon_configs_episode_entry import (
    NEXT_CHAPTER,
    READY_TO_FIGHT,
)
from tasks.dungeon.assets.assets_dungeon_configs_side_story_entry import SUPPORTER_CHECK
from tasks.dungeon.assets.assets_dungeon_episode_battle import (
    EPISODE_AUTO_COMBAT_UNAVAILABLE,
    EPISODE_BATTLE_CONTROLS_CHECK,
    EPISODE_STORY_TEAM_CHECK,
    OCR_EPISODE_LAST_STAR_CONDITION,
    OCR_EPISODE_STAMINA_COST,
)
from tasks.dungeon.assets.assets_dungeon_episode_map import (
    EPISODE_EXPLORATION_CHECK,
    EPISODE_MAP_ACTION,
    EPISODE_MAP_CHECK,
    EPISODE_STAGE_COMPLETE,
    EPISODE_STAGE_LOCKED,
    OCR_EPISODE_STAGE_LIST,
)
from tasks.dungeon.assets.assets_dungeon_episode_reward import (
    EPISODE_CHAPTER_REWARD,
    EPISODE_STORY_COMPLETE,
)
from tasks.dungeon.assets.assets_dungeon_episode_story import (
    EPISODE_STORY_SKIP,
    EPISODE_STORY_SKIP_CHECK,
    EPISODE_STORY_SKIP_CONFIRM,
)
from tasks.dungeon.assets.assets_dungeon_repeat_common import REPEAT_COMBAT_MENU
from tasks.dungeon.assets.assets_dungeon_state import (
    COMBAT_RESULT_CLEAR,
    COMBAT_RESULT_FAILED,
)
from tasks.dungeon.entry import CombatEntryMixin
from tasks.dungeon.episode import EpisodeNavigateMixin, OcrEpisodeLabel
from tasks.dungeon.episode_navigation import EPISODE_ENTRIES
from tasks.dungeon.execute import detect_auto_combat_state


@dataclass(frozen=True)
class StoryStage:
    index: int
    area: tuple
    completed: bool
    locked: bool


def parse_stage_index(text):
    """Require the list's numbered prefix, rather than a number in its title."""
    found = re.match(r'^\s*(\d{1,3})\s*[.．、]\s*\S', text)
    return int(found[1]) if found and int(found[1]) > 0 else None


def parse_story_stamina(text):
    """The prepare button displays cost / available, not a recovery counter."""
    # A leading debit mark can accompany the cost on the glowing button.
    # Return its positive spending amount; signs on available stamina and
    # additional tokens remain invalid rather than guessing a usable balance.
    found = re.fullmatch(r'\s*-?\s*(\d+)\s*/\s*(\d+)\s*', text)
    if not found or int(found[1]) <= 0:
        return None
    return int(found[1]), int(found[2])


class Episode(EpisodeNavigateMixin, CombatEntryMixin, UI):
    ACTION_INTERVAL = 2
    NAVIGATION_TIMEOUT = EpisodeNavigateMixin.EPISODE_NAVIGATE_TIMEOUT_SECONDS
    COMBAT_CHECK_SIMILARITY = 0.8
    MAX_SCROLLS = 6

    def _ocr_lang(self):
        return resolve_ocr_lang(self.config)

    def _text(self, asset):
        text = OcrEpisodeLabel(asset, lang=self._ocr_lang()).ocr_single_line(self.device.image)
        logger.attr(asset.name, text)
        return text

    def _map_ready(self):
        return (
            self.match_template_color(EPISODE_MAP_CHECK, threshold=25)
            and EPISODE_MAP_ACTION.match_color(self.device.image, threshold=25)
        )

    def _map_identity(self):
        episode = self._ocr_episode_number()
        chapter_text = self._ocr_episode_chapter_text()
        chapter = re.match(r'(?:第\s*)?(\d{1,2})(?:\s*章|[.．、\s])', chapter_text)
        selected = parse_stage_index(self._ocr_episode_current_stage_text())
        if episode and chapter:
            return (episode, int(chapter[1])), selected
        return None, selected

    def _read_stages(self):
        complete = EPISODE_STAGE_COMPLETE.match_multi_template(self.device.image)
        locked = EPISODE_STAGE_LOCKED.match_multi_template(self.device.image)
        results = OcrEpisodeLabel(
            OCR_EPISODE_STAGE_LIST, lang=self._ocr_lang(),
        ).detect_and_ocr(self.device.image)
        left, top, right, bottom = OCR_EPISODE_STAGE_LIST.area
        stages = []
        for result in results:
            index = parse_stage_index(result.ocr_text)
            x1, y1, x2, y2 = result.box
            # A partially visible neighbouring title cannot establish its row
            # identity. Keep its original geometry; clipping would hide that
            # the numbered prefix or the status badge may be offscreen.
            if index is None or not (left <= x1 < x2 < right and top + 3 < y1 < y2 < bottom - 3):
                continue
            center = (y1 + y2) / 2

            def same_row(marker, center=center):
                marker_y = (marker.area[1] + marker.area[3]) / 2
                return -5 <= marker_y - center <= 35

            stages.append(StoryStage(index, tuple(result.box),
                                     any(same_row(m) for m in complete),
                                     any(same_row(m) for m in locked)))
        stages.sort(key=lambda row: row.index)
        logger.attr('EpisodeStages', [(s.index, s.completed, s.locked) for s in stages])
        return stages

    def _prepare_stamina(self):
        text = Ocr(OCR_EPISODE_STAMINA_COST, lang=self._ocr_lang()).ocr_single_line(self.device.image)
        logger.attr('EpisodeCostAvailable', text)
        return parse_story_stamina(text)

    def _last_star_condition(self):
        text = Ocr(OCR_EPISODE_LAST_STAR_CONDITION, lang=self._ocr_lang()).ocr_single_line(self.device.image)
        logger.attr('EpisodeLastStarCondition', text)
        return re.sub(r'\s+', '', text)

    def _auto_combat_unavailable(self):
        # A gray auto icon beside bright battle controls means automatic
        # combat is forbidden, not merely switched off. Template correlation
        # alone ignores that brightness distinction. Check the fixed colors
        # too, and reject overlays that dim the entire toolbar at settlement.
        return (
            self.appear(EPISODE_AUTO_COMBAT_UNAVAILABLE)
            and self.match_color(EPISODE_AUTO_COMBAT_UNAVAILABLE, threshold=15)
            and self.match_color(EPISODE_BATTLE_CONTROLS_CHECK, threshold=25)
        )

    def _click(self, button):
        if self.interval_is_reached(button, interval=self.ACTION_INTERVAL):
            self.device.click(button)
            self.interval_reset(button, interval=self.ACTION_INTERVAL)
            return True
        return False

    def run(self):
        """Continue unlocked main-story nodes using the currently configured team.

        Pages:
            in: main, episode map, chapter selector, or an interrupted story/battle
            out: stable episode map, chapter selector, or a state requiring attention
        """
        if server.lang not in ('cn', 'global_cn'):
            logger.info(f'Episode: unsupported asset language {server.lang}')
            return False
        number = int(self.config.Episode_Number)
        limit = int(self.config.Episode_MaxStages)
        if number not in (0, *EPISODE_ENTRIES) or limit < 0:
            raise ValueError('Episode: invalid episode number or stage limit')
        logger.attr('EpisodeModule', __file__)
        logger.attr('EpisodeAssetLanguage', server.lang)
        self.device.screenshot()
        self.completed_stages = 0
        self.stop_reason = self._push(number, limit)
        logger.info(f'Episode: stopped ({self.stop_reason}), completed nodes={self.completed_stages}')
        if self.stop_reason == 'navigation_timeout':
            self._save_navigation_diagnostics()
        return self.stop_reason == 'stage_limit'

    def _save_navigation_diagnostics(self):
        # Keep the exact observation that caused the timeout. A fresh capture
        # here could hide a stale/incorrect device frame or an unhandled page.
        # Record the effective cached files, not just the global language: a
        # correct server.lang does not prove a wrapper refreshed its buttons.
        logger.warning('Episode: navigation timed out; recording the last observed frame')
        logger.attr('EpisodeSupporterTemplates', [button.file for button in SUPPORTER_CHECK.buttons])
        logger.attr('EpisodeChooseTeamTemplates', [button.file for button in CHOOSE_TEAM.buttons])
        logger.attr('EpisodeSupporterPage', self._is_episode_supporter_page())
        logger.attr('EpisodeChooseTeamVisible', self.appear(CHOOSE_TEAM))
        self.device.save_screenshot(genre='episode_timeout', interval=0)
        logger.info(f'Episode: timeout frame saved under {self.config.SCREEN_SHOT_SAVE_FOLDER}/episode_timeout')

    def _push(self, number=0, limit=0, skip_first_screenshot=True):
        """One observation loop owns navigation, story, battle and settlement.

        Pages:
            in: main or any supported episode substate; first frame prepared
            out: unlocked-stage map or a positively identified blocking state
        """
        timeout = Timer(self.NAVIGATION_TIMEOUT, count=0).start()
        star_condition_timeout = Timer(12, count=0).clear()
        stamina_timeout = Timer(12, count=0).clear()
        stamina_values = None
        stamina_matches = 0
        chapter_timeout = Timer(12, count=0).clear()
        completed = set()
        pending = None
        entered = False
        result_seen = False
        exhausted_chapters = set()
        used_chapter_entries = set()
        pending_chapter_entry = None
        last_signature = None
        last_identity = None
        scrolls = 0
        active_episode = number or None

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            self._raise_if_package_full()
            if self.appear(COMBAT_RESULT_FAILED):
                raise RequestHumanTakeover('Episode: battle failed; adjust the team before continuing')
            if self._auto_combat_unavailable():
                message = (
                    'Episode: automatic combat is unavailable in this battle. '
                    'Please complete it manually, then restart this tool.'
                )
                logger.critical(message)
                raise RequestHumanTakeover(message)
            # The selected map's exploration entry requires manual play.
            # Stop before map/result bookkeeping or any navigation action;
            # its green crossed-swords icon alone also appears on normal stages.
            if self.match_template_color(EPISODE_EXPLORATION_CHECK, threshold=25):
                message = (
                    'Episode: exploration (探险) stage requires manual completion. '
                    'Please complete it manually, then restart this tool.'
                )
                logger.critical(message)
                raise RequestHumanTakeover(message)
            # Block only the intersection of a story-team marker and 攻略 in
            # the LAST star condition. A normal team may satisfy that condition,
            # while a supplied single-hero team can have no such restriction.
            # Check the current frame before stamina, clicks and result/map
            # bookkeeping; an empty OCR read is not evidence of permission.
            # Keep the shared prepare marker's color guard so overlays cannot
            # turn obscured mission text into an actionable observation.
            prepare = self.match_template_color(
                REPEAT_COMBAT_MENU, similarity=self.COMBAT_CHECK_SIMILARITY, threshold=25,
            )
            # Confirmed stamina belongs only to this uninterrupted prepare
            # view. A click may fail without changing the page, so keep the
            # reading across retries; leaving prepare invalidates both the
            # cached balance and any partial confirmation/timeout. Otherwise
            # a later stage could start using the previous stage's stamina.
            if not prepare:
                stamina_values, stamina_matches = None, 0
                stamina_timeout.clear()
            if prepare and self.appear(EPISODE_STORY_TEAM_CHECK):
                condition = self._last_star_condition()
                if not condition:
                    if not star_condition_timeout.started():
                        star_condition_timeout.start()
                    elif star_condition_timeout.reached():
                        return 'unreadable_star_condition'
                    continue
                star_condition_timeout.clear()
                if '攻略' in condition:
                    logger.critical(
                        'Episode: story team with 攻略 in the last three-star condition. '
                        'Please complete this stage manually '
                        'with a team that meets the three-star conditions, then restart this tool.'
                    )
                    raise RequestHumanTakeover
            else:
                # The timer belongs only to an unreadable story-team prepare
                # page. A normal team must proceed without OCR and must clear
                # old waiting time before another story-team page appears.
                star_condition_timeout.clear()

            # A start click is only an intention. Keep the pending node through
            # repeated prepare frames, cinematics, and chained reward dialogs.
            # Count once only after a result AND an unobscured map are seen;
            # neither repeated rewards nor an entry click can count a clear.
            if self._map_ready():
                chapter_timeout.clear()
                identity, selected = self._map_identity()
                if identity is None:
                    if timeout.reached():
                        return 'unreadable_map'
                    continue
                if identity != last_identity:
                    # Scroll evidence belongs to one chapter. Reusing the old
                    # signature after NEXT_CHAPTER could skip an unseen list.
                    scrolls, last_signature = 0, None
                    last_identity = identity
                    timeout.reset()
                active_episode = number or identity[0]
                if pending_chapter_entry is not None:
                    used_chapter_entries.add(pending_chapter_entry)
                    pending_chapter_entry = None
                if pending is not None and result_seen:
                    completed.add(pending)
                    self.completed_stages += 1
                    pending, entered, result_seen = None, False, False
                    timeout.reset()
                    scrolls, last_signature = 0, None
                if limit and self.completed_stages >= limit:
                    return 'stage_limit'
                if not entered and timeout.reached():
                    return 'navigation_timeout'
                if number and identity[0] != number:
                    # BACK leaves adventure. The blue map control opens the
                    # episode selector; require its current-frame match before
                    # clicking, then confirm the selected panel on a new frame.
                    self._handle_episode_switch()
                    continue
                if identity in exhausted_chapters:
                    if self.appear(NEXT_CHAPTER):
                        self._click(NEXT_CHAPTER)
                    continue
                rows = self._read_stages()
                candidates = [s for s in rows if not s.completed and not s.locked
                              and (*identity, s.index) not in completed]
                if candidates:
                    target = candidates[0]
                    key = (*identity, target.index)
                    if pending is not None and entered:
                        if timeout.reached():
                            return 'result_unconfirmed'
                        continue
                    if selected == target.index:
                        if self._click(READY_TO_FIGHT):
                            pending = key
                    else:
                        self._click(ClickButton(target.area, name=f'EPISODE_STAGE_{target.index}'))
                    if timeout.reached():
                        return 'navigation_timeout'
                    continue
                if rows and all(row.completed or row.locked or (*identity, row.index) in completed for row in rows):
                    signature = tuple((s.index, s.completed, s.locked) for s in rows)
                    if not self.interval_is_reached(OCR_EPISODE_STAGE_LIST, interval=2):
                        continue
                    if signature == last_signature or scrolls >= self.MAX_SCROLLS:
                        if any(s.locked for s in rows):
                            return 'locked_stage'
                        exhausted_chapters.add(identity)
                        if self.appear(NEXT_CHAPTER):
                            self._click(NEXT_CHAPTER)
                        continue
                    if self.interval_is_reached(OCR_EPISODE_STAGE_LIST, interval=2):
                        self._swipe_episode_stage_list(area=OCR_EPISODE_STAGE_LIST.area)
                        self.interval_reset(OCR_EPISODE_STAGE_LIST, interval=2)
                        last_signature, scrolls = signature, scrolls + 1
                    continue
                if timeout.reached():
                    return 'unreadable_stages'

            if self.appear(EPISODE_STORY_SKIP_CHECK):
                if self.appear(EPISODE_STORY_SKIP_CONFIRM):
                    self._click(EPISODE_STORY_SKIP_CONFIRM)
                continue
            if self.appear(EPISODE_STORY_COMPLETE):
                pending = pending or ('resumed_story',)
                result_seen = True
                self._click(EPISODE_STORY_COMPLETE)
                continue
            if self.appear(EPISODE_CHAPTER_REWARD):
                self._click(EPISODE_CHAPTER_REWARD)
                continue
            if self.appear(COMBAT_RESULT_CONFIRM):
                pending = pending or ('resumed_result',)
                result_seen = True
                self._click(COMBAT_RESULT_CONFIRM)
                continue
            if self.appear(COMBAT_RESULT_CLEAR):
                # As in normal foreground combat, CLEAR is positive result
                # evidence. A fast transition or manual dismissal may hide
                # the next reward frame; requiring that frame would strand a
                # pending node after returning to the map. Do not count here:
                # repeated clear frames still belong to the same pending node.
                pending = pending or ('resumed_result',)
                result_seen = True
                self._click(COMBAT_RESULT_CLEAR)
                continue
            if self.appear(EPISODE_STORY_SKIP):
                pending = pending or ('resumed_story',)
                entered = True
                self._click(EPISODE_STORY_SKIP)
                continue
            # Episode 4 identifies prepare by the repeat-menu icon. Team-power
            # text and the glowing mission label vary between captured frames
            # and can fail even on an idle prepare page. Reuse the shared
            # marker at its existing combat similarity, retaining color to
            # reject a prepare page dimmed by an overlay before reading cost.
            if prepare:
                entered = True
                # Preserve the existing first valid affordability check and
                # start timing. After three consecutive equal (cost, balance)
                # reads, reuse that pair rather than OCR every retry frame.
                # A changed or invalid read breaks the streak, so intermittent
                # recognition failures cannot count as stable observations.
                if stamina_matches < 3:
                    values = self._prepare_stamina()
                    if values is None:
                        stamina_values, stamina_matches = None, 0
                        if not stamina_timeout.started():
                            stamina_timeout.start()
                        elif stamina_timeout.reached():
                            return 'unreadable_stamina'
                        continue
                    stamina_timeout.clear()
                    if values == stamina_values:
                        stamina_matches += 1
                    else:
                        stamina_values, stamina_matches = values, 1
                cost, available = stamina_values
                if available < cost:
                    return 'no_stamina'
                if pending is None:
                    pending = ('resumed_battle',)
                if self.appear(COMBAT_START):
                    self._click(COMBAT_START)
                continue
            if self._is_episode_supporter_page():
                entered = True
                if self.appear(CHOOSE_TEAM):
                    self._click(CHOOSE_TEAM)
                continue
            auto = detect_auto_combat_state(self.appear)
            if auto is not None:
                entered = True
                self.device.stuck_record_clear()
                if auto is False:
                    self.device.click_record_remove(AUTO_COMBAT)
                    self._click(AUTO_COMBAT)
                continue

            selected_episode = self._selector_episode()
            if self._is_episode_selector():
                if timeout.reached():
                    return 'navigation_timeout'
                active_episode = active_episode or selected_episode
                if active_episode is None:
                    # Prelude/side-story panels do not identify a numbered
                    # episode. A missing title may also be a loading frame.
                    # With no configured target, wait for positive identity
                    # within the navigation deadline instead of guessing.
                    continue
                if selected_episode != active_episode:
                    chapter_timeout.clear()
                    entry, _ = EPISODE_ENTRIES[active_episode]
                    if self.appear(entry):
                        self._click(entry)
                else:
                    entries = [b for b in self._chapter_entries() if (active_episode, b.area) not in used_chapter_entries]
                    if pending_chapter_entry is not None:
                        entries = [b for b in entries if (active_episode, b.area) == pending_chapter_entry]
                    if entries:
                        chapter_timeout.clear()
                        button = entries[0]
                        if self._click(button):
                            # Consuming an entry on click would skip a chapter
                            # whenever a click is ignored. Commit it only after
                            # a positively recognized destination map appears.
                            pending_chapter_entry = (active_episode, button.area)
                    # A selector without an available arrow may be loading,
                    # locked, or outside this viewport. It never proves that
                    # the story is complete. Allow delayed arrows to appear;
                    # a stable unavailable selector stops without success.
                    elif not chapter_timeout.started():
                        chapter_timeout.start()
                    elif chapter_timeout.reached():
                        return 'chapter_unavailable'
                continue

            if self.is_in_main(interval=0):
                # The public page route returns to a map which this loop owns.
                self.ui_goto(page_episode, skip_first_screenshot=True)
                skip_first_screenshot = True
                timeout.reset()
                continue
            if self._handle_dungeon_network_error(interval=self.ACTION_INTERVAL):
                timeout.reset()
                continue
            if not entered and timeout.reached():
                return 'navigation_timeout'

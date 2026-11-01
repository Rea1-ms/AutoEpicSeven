"""Continue the selected side story; the player opens its list before starting."""

import re

import numpy as np

from module.base.button import ClickButton
from module.base.timer import Timer
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.ocr.ocr import Ocr
from tasks.dungeon.assets.assets_dungeon_action import (
    AUTO_COMBAT,
    CHOOSE_TEAM,
    COMBAT_RESULT_CONFIRM,
    COMBAT_START,
)
from tasks.dungeon.assets.assets_dungeon_repeat_common import REPEAT_COMBAT_MENU
from tasks.dungeon.assets.assets_dungeon_state import (
    COMBAT_RESULT_CLEAR,
    COMBAT_RESULT_FAILED,
)
from tasks.dungeon.assets.assets_dungeon_story_push_side_story import (
    OCR_SIDE_STORY_CHAPTER,
    OCR_SIDE_STORY_COST,
    SIDE_STORY_COMPLETE_CHECKMARK,
    SIDE_STORY_CURRENT,
    SIDE_STORY_LIST_CHECK,
    SIDE_STORY_READY,
    SIDE_STORY_WATCH,
)


class SideStoryPushMixin:
    def _side_story_complete(self):
        # The final selected node retains a green completion badge. Earlier
        # completed rows carry the same badge, so its position must belong to
        # the CURRENT selected icon, located again in this frame. Reassign the
        # search on every call; never reuse the previous row after scrolling.
        # The finished list has a different watch-button layout and no cost;
        # do not require the ordinary actionable-list guard or OCR to exit.
        if not self.match_template_luma(SIDE_STORY_LIST_CHECK) or not self.appear(SIDE_STORY_CURRENT):
            return False
        SIDE_STORY_COMPLETE_CHECKMARK.load_search(SIDE_STORY_CURRENT.button)
        return self.match_template_color(SIDE_STORY_COMPLETE_CHECKMARK, threshold=25)

    def _side_story_list_ready(self):
        # The transparent header follows each story's background. Its color
        # cannot identify an unobscured page; the bright action owns that guard.
        return self.match_template_luma(SIDE_STORY_LIST_CHECK) and (
            self.match_template_color(SIDE_STORY_WATCH, threshold=25)
            or self.match_template_color(SIDE_STORY_READY, threshold=25)
        )

    def _side_story_identity(self):
        # The blue selected marker is translucent over the story art. Match
        # its shape here; list_ready already confirms an unobscured action.
        if not self.appear(SIDE_STORY_CURRENT):
            return None
        # Wrapper.area is the source rectangle; the matched button carries
        # this frame's translated row. Never reuse the source row on scroll.
        _, top, _, bottom = SIDE_STORY_CURRENT.button
        center = (top + bottom) // 2
        title_area = ClickButton((960, max(135, center - 25), 1183, min(614, center + 25)),
                                 name='SIDE_STORY_CURRENT_TITLE')
        lang = self._ocr_lang()
        # Recheck the selected marker and the complete chapter/title glyphs on
        # every usable list frame. Background art, stamina and button animation
        # do not change node identity and must not cause another OCR request.
        # In particular, OCR spelling jitter on an unchanged paid-watch node
        # must not masquerade as selection advancement. Geometry is captured
        # from this frame above, so scrolling still observes the selected row.
        view = (lang, *(np.all(self.image_crop(area, copy=False) > 180, axis=2).tobytes()
                        for area in (OCR_SIDE_STORY_CHAPTER, title_area)))
        cached = getattr(self, '_side_story_identity_cache', None)
        if cached is not None and cached[0] == view:
            return cached[1]
        chapter = Ocr(OCR_SIDE_STORY_CHAPTER, lang=lang).ocr_single_line(self.device.image)
        title = Ocr(title_area, lang=lang).ocr_single_line(self.device.image)
        chapter, title = re.sub(r'\s+', '', chapter), re.sub(r'\s+', '', title)
        logger.attr('SideStoryCurrent', (chapter, title))
        identity = (chapter, title) if re.fullmatch(r'第\d+章', chapter) and title else None
        # Never cache failed reads: the same pixels may become readable on a
        # later recognition attempt. The old text cannot authorize changed art.
        self._side_story_identity_cache = (view, identity) if identity is not None else None
        return identity

    def _side_story_cost(self):
        text = Ocr(OCR_SIDE_STORY_COST, lang=self._ocr_lang()).ocr_single_line(self.device.image)
        logger.attr('SideStoryCostAvailable', text)
        found = re.fullmatch(r'\s*(\d+)\s*/\s*(\d+)\s*', text)
        return (int(found[1]), int(found[2])) if found and int(found[1]) > 0 else None

    def _push_side_story(self, limit=0, skip_first_screenshot=True):
        """Advance the game's current selection without entering activity menus.

        Pages:
            in: an opened story list, or its interrupted story/prepare/battle
            out: completed/next selected list at the node limit, or a blocking state
        """
        self._reset_story_intro()
        self._side_story_identity_cache = None
        timeout = Timer(self.NAVIGATION_TIMEOUT, count=0).start()
        cost_timeout = Timer(12, count=0).clear()
        pending = None
        entered = False
        result_seen = False
        completed = set()
        stamina_values = None
        stamina_matches = 0

        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            self._raise_if_package_full()
            if self.appear(COMBAT_RESULT_FAILED):
                raise RequestHumanTakeover('Story: battle failed; adjust the team before continuing')
            if self._auto_combat_unavailable():
                raise RequestHumanTakeover('Story: automatic combat is unavailable; complete the battle manually')

            if self._side_story_complete():
                self._reset_story_intro()
                self._side_story_identity_cache = None
                # The badge is positive completion evidence even though the
                # final node remains selected. Count only a node whose entry
                # was observed in this loop; starting on an already completed
                # list or invoking the loop again must not invent another clear.
                if entered:
                    self.completed_stages += 1
                return 'story_complete'

            if self._side_story_list_ready():
                self._reset_story_intro()
                identity = self._side_story_identity()
                if identity is None:
                    if timeout.reached():
                        return 'unreadable_story'
                    continue
                # The game selects the next node after both plain stories and
                # battles. That changed, fully read list identity is completion
                # evidence even if no reward/CLEAR frame was sampled. A click
                # alone, repeated old list, or changed animation is not enough.
                # Keep the original request through delayed frames; resetting
                # it on every click would repeat paid watches or double-count.
                if entered and (pending is None or identity != pending or result_seen):
                    if pending is not None:
                        completed.add(pending)
                    self.completed_stages += 1
                    pending, entered, result_seen = None, False, False
                    timeout.reset()
                    if limit and self.completed_stages >= limit:
                        return 'stage_limit'
                if identity in completed:
                    return 'story_complete'
                if entered:
                    if timeout.reached():
                        return 'result_unconfirmed'
                    continue
                if pending is not None and identity != pending:
                    # A different selection without observing story/battle
                    # entry cannot prove completion of the submitted request.
                    if timeout.reached():
                        return 'result_unconfirmed'
                    continue
                if timeout.reached():
                    return 'navigation_timeout'
                if self.match_template_color(SIDE_STORY_WATCH, threshold=25):
                    # After dispatch, even a zero/unreadable balance can be
                    # the successful debit arriving before the story opens.
                    # Cost authorizes a NEW request only; never re-evaluate it
                    # while the original watch is still awaiting entry.
                    if pending is not None:
                        continue
                    cost = self._side_story_cost()
                    if cost is None:
                        if not cost_timeout.started():
                            cost_timeout.start()
                        elif cost_timeout.reached():
                            return 'unreadable_stamina'
                        continue
                    cost_timeout.clear()
                    if cost[1] < cost[0]:
                        return 'no_stamina'
                    if pending is None and self._click(SIDE_STORY_WATCH):
                        # Watching spends stamina immediately. Dispatch once
                        # and await a story/list change; a repeated old frame
                        # cannot prove that the debit did not happen.
                        pending = identity
                elif self.match_template_color(SIDE_STORY_READY, threshold=25):
                    if self._click(SIDE_STORY_READY):
                        pending = identity
                continue

            if self._story_visible():
                # A positive departure ends this list visit. Unknown frames
                # and ignored entry clicks alone do not invalidate its text.
                self._side_story_identity_cache = None
                entered = True
                self._handle_story_skip()
                continue
            if self.appear(COMBAT_RESULT_CONFIRM) or self.appear(COMBAT_RESULT_CLEAR):
                self._side_story_identity_cache = None
                self._reset_story_intro()
                if not result_seen:
                    timeout.reset()
                entered, result_seen = True, True
                button = COMBAT_RESULT_CONFIRM if self.appear(COMBAT_RESULT_CONFIRM) else COMBAT_RESULT_CLEAR
                self._click(button)
                continue

            prepare = self.match_template_color(
                REPEAT_COMBAT_MENU, similarity=self.COMBAT_CHECK_SIMILARITY, threshold=25,
            )
            if not prepare:
                stamina_values, stamina_matches = None, 0
                cost_timeout.clear()
            if prepare:
                self._side_story_identity_cache = None
                self._reset_story_intro()
                # Side-story mode intentionally has no main-story star/team
                # condition OCR. Affordability still belongs to this exact
                # prepare view and is invalidated as soon as that view ends.
                if stamina_matches < 3:
                    cost = self._prepare_stamina()
                    if cost is None:
                        stamina_values, stamina_matches = None, 0
                        if not cost_timeout.started():
                            cost_timeout.start()
                        elif cost_timeout.reached():
                            return 'unreadable_stamina'
                        continue
                    cost_timeout.clear()
                    stamina_matches = stamina_matches + 1 if cost == stamina_values else 1
                    stamina_values = cost
                if stamina_values[1] < stamina_values[0]:
                    return 'no_stamina'
                entered = True
                if self.appear(COMBAT_START):
                    self._click(COMBAT_START)
                continue
            if self._is_episode_supporter_page():
                self._side_story_identity_cache = None
                self._reset_story_intro()
                if self.appear(CHOOSE_TEAM):
                    self._click(CHOOSE_TEAM)
                continue
            auto = self._story_auto_state()
            if auto is not None:
                self._side_story_identity_cache = None
                self._reset_story_intro()
                entered = True
                self.device.stuck_record_clear()
                if auto is False:
                    self.device.click_record_remove(AUTO_COMBAT)
                    self._click(AUTO_COMBAT)
                continue
            if self.is_in_main(interval=0) or self._map_ready() or self._is_episode_selector():
                return 'wrong_start_page'
            if self._handle_dungeon_network_error(interval=self.ACTION_INTERVAL):
                continue
            if not entered and timeout.reached():
                return 'navigation_timeout'

"""Read profession bonuses directly from the lobby's harmony relic tree."""

from module.base.timer import Timer
from module.base.utils import color_similar, get_color
from module.logger import logger
from tasks.base.assets.assets_base_page import BACK
from tasks.brawl.assets.assets_brawl import LOBBY_CHECK, LOBBY_EMPTY_CHECK
from tasks.brawl.assets.assets_brawl_entry import LOBBY_CONTINUE, LOBBY_ENTER
from tasks.brawl.assets.assets_brawl_relics import (
    HARMONY_SELECTED,
    HARMONY_TAB,
    LOBBY_RELICS,
    NODE_ACTIVE,
    NODE_AVAILABLE,
    NODE_INACTIVE,
    PAUSE_BUTTON,
    PAUSE_MENU_CHECK,
    PAUSE_RETURN,
    RELIC_EFFECTS_CHECK,
    RELIC_KNIGHT,
    RELIC_MAGE,
    RELIC_PAGE_CHECK,
    RELIC_RANGER,
    RELIC_SOUL_WEAVER,
    RELIC_THIEF,
    RELIC_WARRIOR,
    SAVE_EXIT,
)
from tasks.brawl.entry import BrawlEntry
from tasks.brawl.vision import BrawlScreen, BrawlVision, ObservationError


_NODES = (
    ("骑士", RELIC_KNIGHT, (479, 254)),
    ("精灵师", RELIC_SOUL_WEAVER, (649, 254)),
    ("射手", RELIC_RANGER, (479, 394)),
    ("盗贼", RELIC_THIEF, (649, 394)),
    ("魔导士", RELIC_MAGE, (479, 534)),
    ("战士", RELIC_WARRIOR, (649, 534)),
)


def relic_page(image):
    """Require both fixed page headings, including their unobscured colors."""
    vision = BrawlVision(image)
    return (vision.matches(RELIC_PAGE_CHECK, color=True)
            and vision.matches(RELIC_EFFECTS_CHECK, color=True))


def harmony_selected(image):
    return (HARMONY_SELECTED.match_template(image, similarity=0.92)
            and HARMONY_SELECTED.match_color(image, threshold=15))


def pause_menu(image):
    vision = BrawlVision(image)
    return (vision.matches(PAUSE_MENU_CHECK, color=True)
            and vision.matches(SAVE_EXIT, color=True)
            and vision.matches(PAUSE_RETURN, color=True))


def read_relic_talents(image):
    """Return six explicit node states without OCR or any field contribution.

    Each fixed node must retain its profession glyph and a known top border.
    Blue means activated; photographed gray/white borders mean inactive. An
    absent glyph, black obstruction or unknown color never becomes zero. The
    controller observes the complete mapping twice before leaving the page.
    """
    if not relic_page(image) or not harmony_selected(image):
        raise ObservationError("The harmony relic page is not visibly selected")
    bonuses = {}
    for name, icon, (x, y) in _NODES:
        if not icon.match_template_luma(image, similarity=0.92) or not icon.is_offset_in(x=2, y=2):
            raise ObservationError(f"The {icon.name} profession glyph is unread or misplaced")
        color = get_color(image, (x - 12, y - 35, x + 13, y - 32))
        active = color_similar(color, NODE_ACTIVE.color, threshold=15)
        inactive = any(color_similar(color, reference.color, threshold=20)
                       for reference in (NODE_INACTIVE, NODE_AVAILABLE))
        if active == inactive:
            raise ObservationError(f"The {icon.name} activation border is unread or contradictory")
        bonuses[name] = int(active)
    return bonuses


class BrawlRelics(BrawlEntry):
    def read(self, skip_first_screenshot=True):
        """Read six relic talents and return to the original scene.

        Pages: shared navigation, lobby, relics or stable preparation in. A
        preparation is saved through Pause / Save and exit, then continued only
        from a verified saved lobby. Its original panel and mode must agree on
        return. Other starting pages finish on the lobby. No relic node, battle,
        purchase, formation or new-run control is clicked here.
        """
        if not skip_first_screenshot:
            self.device.screenshot()
        image = self.device.image
        current = BrawlVision(image).observe(read_panel=False)
        if not relic_page(image) and not pause_menu(image):
            if current.state == BrawlScreen.UNKNOWN and current.game_mode is None:
                from tasks.base.page import page_brawl_lobby

                if self.ui_ensure(page_brawl_lobby, acquire_lang_checked=False, skip_first_screenshot=True):
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
            elif current.state not in (BrawlScreen.PREPARATION, BrawlScreen.LOBBY):
                raise ObservationError("Relic reading requires a stable preparation, lobby or navigation page")

        timeout = Timer(self.ENTRY_TIMEOUT).start()
        original = None
        unpausing = pausing = saving = False
        opened = selected = returning = resuming = False
        lobby_saved = None
        previous = bonuses = None
        reason = "No complete harmony talent observation"
        first = True
        while True:
            if first:
                first = False
            else:
                self.device.screenshot()
            image = self.device.image
            vision = BrawlVision(image)
            menu = pause_menu(image)
            page = relic_page(image)
            # A pause overlay can leave underlying preparation templates visible.
            # Classify it first, without OCR of the darkened background.
            current = vision.observe(read_panel=False) if not menu and not page else None
            ready = self.interval_is_reached(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)

            # A positively verified return wins over a deadline on that same
            # frame. Unread panels still pass through the bounded wait below.
            if current is not None and current.state == BrawlScreen.PREPARATION and resuming:
                restored = vision.observe()
                if restored.panel is None or restored.game_mode is None:
                    reason = restored.reason or "The continued preparation is unread"
                elif restored.panel != original.panel or restored.game_mode != original.game_mode:
                    raise ObservationError("The saved preparation changed during the relic visit")
                else:
                    logger.info(f"Class talents confirmed from harmony relics: {bonuses}")
                    return bonuses
            if current is not None and current.state == BrawlScreen.LOBBY:
                saved = vision.matches(LOBBY_CHECK, color=True) and vision.matches(LOBBY_CONTINUE, color=True)
                empty = vision.matches(LOBBY_EMPTY_CHECK, color=True) and vision.matches(LOBBY_ENTER, color=True)
                if saved == empty:
                    reason = "The lobby save controls are unread or contradictory"
                else:
                    if original is not None and (not saving or not saved):
                        raise ObservationError("The original saved run is not available in the lobby")
                    if lobby_saved is None:
                        lobby_saved = saved
                    elif saved != lobby_saved:
                        raise ObservationError("The lobby save changed during the relic visit")
                    if returning and original is None:
                        logger.info(f"Class talents confirmed from harmony relics: {bonuses}")
                        return bonuses
            if timeout.reached():
                raise ObservationError(f"The relic visit did not reach its verified return: {reason}")

            if menu:
                if opened or returning:
                    raise ObservationError("An unexpected pause menu interrupted the relic visit")
                if original is None:
                    # Starting on Pause has no trustworthy unmasked panel yet.
                    # Close it once, acquire that panel, then use the normal save
                    # path. Never infer original resources from dimmed digits.
                    if not unpausing and ready:
                        self.device.click(PAUSE_RETURN)
                        self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                        unpausing = True
                        timeout.reset()
                elif pausing and not saving and ready:
                    self.device.click(SAVE_EXIT)
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                    saving = True
                    timeout.reset()
                continue

            if current is not None and current.state == BrawlScreen.PREPARATION:
                if resuming:
                    continue
                if pausing:
                    # Pending pause/save frames never authorize another click.
                    continue
                if opened or returning:
                    raise ObservationError("Preparation appeared before the saved-run continuation request")
                if ready and vision.matches(PAUSE_BUTTON, color=True):
                    original = vision.observe()
                    if original.panel is None or original.game_mode is None:
                        reason = original.reason or "The original preparation is unread"
                        original = None
                        continue
                    self.device.click(PAUSE_BUTTON)
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                    pausing = True
                    timeout.reset()
                continue

            if current is not None and current.state == BrawlScreen.LOBBY:
                if saved == empty:
                    continue
                if returning:
                    if not resuming and ready:
                        self.device.click(LOBBY_CONTINUE)
                        self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                        resuming = True
                        timeout.reset()
                    continue
                if not opened and ready and vision.matches(LOBBY_RELICS, color=True):
                    self.device.click(LOBBY_RELICS)
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                    opened = True
                    timeout.reset()
                continue

            if returning:
                # Back/Continue remain pending through repeated relic frames.
                # No extra Back, tab or entry request is permitted.
                if current is not None and current.state != BrawlScreen.UNKNOWN:
                    raise ObservationError("The relic return was interrupted by an unexpected scene")
                continue
            if page:
                if original is not None and (not saving or not opened):
                    raise ObservationError("The relic page appeared before a verified save and lobby")
                if harmony_selected(image):
                    try:
                        values = read_relic_talents(image)
                    except ObservationError as exc:
                        reason, previous = str(exc), None
                        continue
                    if previous != values:
                        previous = values
                        continue
                    bonuses = values
                    if ready and vision.matches(BACK, color=True):
                        self.device.click(BACK)
                        self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                        returning = True
                        timeout.reset()
                    continue
                previous = None
                if not selected and ready and vision.matches(HARMONY_TAB, color=True):
                    self.device.click(HARMONY_TAB)
                    self.interval_reset(self.ACTION_TIMER, interval=self.ACTION_INTERVAL)
                    selected = True
                    timeout.reset()
                continue
            previous = None
            if current.state != BrawlScreen.UNKNOWN:
                raise ObservationError("The relic visit was interrupted by an unexpected scene")

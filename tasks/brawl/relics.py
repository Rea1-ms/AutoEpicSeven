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

                self.ui_ensure(page_brawl_lobby, acquire_lang_checked=False, skip_first_screenshot=True)
            elif current.state not in (BrawlScreen.PREPARATION, BrawlScreen.LOBBY):
                raise ObservationError("Relic reading requires a stable preparation, lobby or navigation page")

        timeout = Timer(self.ENTRY_TIMEOUT).start()
        original = None
        unpausing = pausing = saving = False
        opened = returning = resuming = False
        lobby_saved = None
        previous = bonuses = None
        reason = "No complete harmony talent observation"
        pending = None
        completed = set()
        retry = Timer(self.ACTION_INTERVAL)
        attempts = 0

        def complete(button):
            nonlocal pending
            # A positively observed destination closes its source action even
            # when the visit began on that destination. A later stale source
            # frame must never reopen a completed part of this route.
            completed.add(button.name)
            if pending == button.name:
                pending = None

        def request(button):
            nonlocal pending, attempts
            if button.name in completed or pending not in (None, button.name):
                return False
            repeated = pending == button.name
            if repeated and not retry.reached():
                return False
            self.device.click(button)
            pending = button.name
            attempts = attempts + 1 if repeated else 1
            retry.reset()
            if repeated:
                logger.info(f"Brawl relic navigation retried: action={button.name}, attempt={attempts}")
            else:
                # Only a newly authorized transition owns a fresh deadline.
                # A click without a destination is still pending: retrying it
                # must not keep an unresponsive screen alive indefinitely.
                timeout.reset()
            return True

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
            # Flags record requested transitions, not successful clicks. Only
            # positive destination evidence completes them. Safe navigation can
            # retry on its still-verified source page after its own interval;
            # neither unknown frames nor old completed pages authorize clicks.

            # A positively verified return wins over a deadline on that same
            # frame. Unread panels still pass through the bounded wait below.
            if current is not None and current.state == BrawlScreen.PREPARATION and resuming:
                # Reaching preparation closes Continue even if its digits have
                # not settled yet. A later stale lobby must not retry a click
                # whose destination was already positively observed.
                complete(LOBBY_CONTINUE)
                restored = vision.observe()
                if restored.panel is None or restored.game_mode is None:
                    reason = restored.reason or "The continued preparation is unread"
                elif restored.panel != original.panel or restored.game_mode != original.game_mode:
                    raise ObservationError("The saved preparation changed during the relic visit")
                else:
                    logger.info(f"Class talents confirmed from harmony relics: {bonuses}")
                    return bonuses
            if current is not None and current.state == BrawlScreen.LOBBY:
                # Destination identity closes the old navigation before save
                # controls become readable. Those controls still gate the next
                # action and final result; they cannot revive Save/Back retries.
                if saving:
                    complete(SAVE_EXIT)
                if returning:
                    complete(BACK)
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
                    # Return to preparation before capturing the original
                    # context; never read resources through the dimmed overlay.
                    if request(PAUSE_RETURN):
                        unpausing = True
                elif pausing:
                    complete(PAUSE_BUTTON)
                    if request(SAVE_EXIT):
                        saving = True
                continue

            if current is not None and current.state == BrawlScreen.PREPARATION:
                if resuming:
                    continue
                if saving:
                    # A pause menu already proved the previous pause click.
                    # Seeing preparation again cannot start another save path.
                    continue
                if opened or returning:
                    raise ObservationError("Preparation appeared before the saved-run continuation request")
                if unpausing:
                    complete(PAUSE_RETURN)
                if pausing and not retry.reached():
                    continue
                if vision.matches(PAUSE_BUTTON, color=True):
                    observed = vision.observe()
                    if observed.panel is None or observed.game_mode is None:
                        reason = observed.reason or "The original preparation is unread"
                        continue
                    if original is None:
                        original = observed
                    elif observed.panel != original.panel or observed.game_mode != original.game_mode:
                        raise ObservationError("The original preparation changed while pause was pending")
                    if request(PAUSE_BUTTON):
                        pausing = True
                continue

            if current is not None and current.state == BrawlScreen.LOBBY:
                if saved == empty:
                    continue
                if returning:
                    if request(LOBBY_CONTINUE):
                        resuming = True
                    continue
                if vision.matches(LOBBY_RELICS, color=True) and request(LOBBY_RELICS):
                    opened = True
                continue

            if returning:
                # Retry Back only while its destination is still unseen and
                # the current harmony page retains the complete readout that
                # authorized leaving it. Once the lobby was seen, relic frames
                # cannot send another Back while Continue is pending.
                if page and pending == BACK.name and retry.reached() and harmony_selected(image):
                    try:
                        values = read_relic_talents(image)
                    except ObservationError as exc:
                        reason = str(exc)
                    else:
                        if values == bonuses and vision.matches(BACK, color=True):
                            request(BACK)
                        else:
                            reason = "The harmony talents changed while return was pending"
                if current is not None and current.state != BrawlScreen.UNKNOWN:
                    raise ObservationError("The relic return was interrupted by an unexpected scene")
                continue
            if page:
                if original is not None and (not saving or not opened):
                    raise ObservationError("The relic page appeared before a verified save and lobby")
                complete(LOBBY_RELICS)
                if harmony_selected(image):
                    complete(HARMONY_TAB)
                    try:
                        values = read_relic_talents(image)
                    except ObservationError as exc:
                        reason, previous = str(exc), None
                        continue
                    if previous != values:
                        previous = values
                        continue
                    bonuses = values
                    if vision.matches(BACK, color=True) and request(BACK):
                        returning = True
                    continue
                previous = None
                if vision.matches(HARMONY_TAB, color=True):
                    request(HARMONY_TAB)
                continue
            previous = None
            if current.state != BrawlScreen.UNKNOWN:
                raise ObservationError("The relic visit was interrupted by an unexpected scene")

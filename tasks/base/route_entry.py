from module.base.button import ButtonWrapper, ClickButton
from module.base.utils import area_offset
from module.logger import logger


def match_route_entry(button: ButtonWrapper, image, prefer_lower: bool | None = None) -> ClickButton | None:
    """Locate an entry and return a snapshot of this frame's click coordinates."""
    candidates = list(button.buttons)
    if prefer_lower is not None:
        # Frame numbers have different meanings between servers. The milestone
        # inserts a row above these entries, so use geometric order as a hint.
        # A rank hint never excludes the other layout, even when OCR is valid.
        candidates.sort(key=lambda candidate: candidate.area[1], reverse=prefer_lower)
    for candidate in candidates:
        if not candidate.match_template_luma(image):
            continue
        # Do not click the shared wrapper: it may still select a different asset
        # from an earlier frame. Freeze the successful candidate's coordinates
        # now; later matching attempts can mutate Button offsets even on failure.
        area = area_offset(candidate.area, candidate._button_offset)
        click = ClickButton(area=area, button=candidate.button, name=button.name)
        logger.info(f"UI route entry matched: {button.name}, asset={candidate.file}, button={click.button}")
        return click
    return None

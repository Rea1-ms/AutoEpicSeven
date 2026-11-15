import argparse

import numpy as np


DEFAULT_FRAME_RATE = 30


def parse_frame_rate(value):
    """Validate a positive whole-number capture rate before connecting."""
    try:
        frame_rate = int(str(value))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError('帧率必须是正整数') from exc
    if frame_rate <= 0:
        raise argparse.ArgumentTypeError('帧率必须是正整数')
    return frame_rate


def handle_sensitive_info(image: np.ndarray, copy=True) -> np.ndarray:
    """Return a writable screenshot copy.

    The current game layout does not show an account UID in the lower-left
    corner, so the capture tool must preserve those pixels.  Keep this small
    helper for callers that need a private copy without changing the source
    frame in place.
    """
    if copy:
        image = image.copy()
    return image

import numpy as np


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

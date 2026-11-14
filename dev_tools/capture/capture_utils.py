import numpy as np


def handle_sensitive_info(image: np.ndarray, copy=True) -> np.ndarray:
    """Return an image with the account UID area painted black."""
    if copy:
        image = image.copy()
    image[680:720, 0:180, :] = 0
    return image

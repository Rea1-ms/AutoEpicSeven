from __future__ import annotations

import typing as t

import cv2 as _cv2
import numpy as np


Box = tuple[int, int, int, int]
cv2: t.Any = _cv2


class InteractiveCropper:
    """Small OpenCV editor for selecting multiple boxes from one stable frame."""

    WINDOW_NAME = "AutoEpicSeven Asset Crop"
    CANVAS_SIZE = (1280, 720)
    MIN_ZOOM = 0.25
    MAX_ZOOM = 8.0

    def __init__(
        self,
        image: np.ndarray | None = None,
        frame_getter: t.Callable[[], np.ndarray] | None = None,
    ):
        if image is None and frame_getter is None:
            raise ValueError("image or frame_getter is required")

        self.frame_getter = frame_getter
        self.frame = image.copy() if image is not None else None
        self.frozen = frame_getter is None
        self.boxes: list[Box] = []
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

        self._left_start: tuple[int, int] | None = None
        self._left_current: tuple[int, int] | None = None
        self._right_start: tuple[int, int] | None = None
        self._right_pan_start: tuple[float, float] | None = None

    @staticmethod
    def _wheel_delta(flags: int) -> int:
        value = (flags >> 16) & 0xFFFF
        return value - 0x10000 if value & 0x8000 else value

    def _screen_to_image(self, point: tuple[int, int]) -> tuple[int, int]:
        if self.frame is None:
            return 0, 0
        height, width = self.frame.shape[:2]
        x = round((point[0] - self.pan_x) / self.zoom)
        y = round((point[1] - self.pan_y) / self.zoom)
        return max(0, min(width, x)), max(0, min(height, y))

    def _image_to_screen(self, point: tuple[int, int]) -> tuple[int, int]:
        return (
            round(point[0] * self.zoom + self.pan_x),
            round(point[1] * self.zoom + self.pan_y),
        )

    def _zoom_at(self, point: tuple[int, int], factor: float) -> None:
        image_x = (point[0] - self.pan_x) / self.zoom
        image_y = (point[1] - self.pan_y) / self.zoom
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        self.pan_x = point[0] - image_x * new_zoom
        self.pan_y = point[1] - image_y * new_zoom
        self.zoom = new_zoom

    def _mouse(self, event: int, x: int, y: int, flags: int, _param) -> None:
        if event == cv2.EVENT_MOUSEWHEEL:
            factor = 1.2 if self._wheel_delta(flags) > 0 else 1 / 1.2
            self._zoom_at((x, y), factor)
            return

        if event == cv2.EVENT_RBUTTONDOWN:
            self._right_start = (x, y)
            self._right_pan_start = (self.pan_x, self.pan_y)
            return
        if event == cv2.EVENT_MOUSEMOVE and self._right_start is not None:
            assert self._right_pan_start is not None
            self.pan_x = self._right_pan_start[0] + x - self._right_start[0]
            self.pan_y = self._right_pan_start[1] + y - self._right_start[1]
            return
        if event == cv2.EVENT_RBUTTONUP:
            self._right_start = None
            self._right_pan_start = None
            return

        if not self.frozen:
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            self._left_start = self._screen_to_image((x, y))
            self._left_current = self._left_start
            return
        if event == cv2.EVENT_MOUSEMOVE and self._left_start is not None:
            self._left_current = self._screen_to_image((x, y))
            return
        if event == cv2.EVENT_LBUTTONUP and self._left_start is not None:
            end = self._screen_to_image((x, y))
            x1, x2 = sorted((self._left_start[0], end[0]))
            y1, y2 = sorted((self._left_start[1], end[1]))
            if x2 > x1 and y2 > y1:
                self.boxes.append((x1, y1, x2, y2))
            self._left_start = None
            self._left_current = None

    def _render(self) -> np.ndarray:
        canvas_width, canvas_height = self.CANVAS_SIZE
        canvas = np.zeros((canvas_height, canvas_width, 3), dtype=np.uint8)
        if self.frame is None:
            return canvas

        frame_bgr = cv2.cvtColor(self.frame, cv2.COLOR_RGB2BGR)
        scaled = cv2.resize(
            frame_bgr,
            dsize=None,
            fx=self.zoom,
            fy=self.zoom,
            interpolation=cv2.INTER_NEAREST if self.zoom >= 1 else cv2.INTER_AREA,
        )
        left = round(self.pan_x)
        top = round(self.pan_y)
        dst_x1 = max(0, left)
        dst_y1 = max(0, top)
        dst_x2 = min(canvas_width, left + scaled.shape[1])
        dst_y2 = min(canvas_height, top + scaled.shape[0])
        if dst_x2 > dst_x1 and dst_y2 > dst_y1:
            src_x1 = dst_x1 - left
            src_y1 = dst_y1 - top
            src_x2 = src_x1 + dst_x2 - dst_x1
            src_y2 = src_y1 + dst_y2 - dst_y1
            canvas[dst_y1:dst_y2, dst_x1:dst_x2] = scaled[
                src_y1:src_y2,
                src_x1:src_x2,
            ]

        for index, box in enumerate(self.boxes, start=1):
            start = self._image_to_screen((box[0], box[1]))
            end = self._image_to_screen((box[2], box[3]))
            cv2.rectangle(canvas, start, end, (64, 255, 64), 2)
            cv2.putText(
                canvas,
                str(index),
                (start[0] + 4, start[1] + 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (64, 255, 64),
                1,
                cv2.LINE_AA,
            )

        if self._left_start is not None and self._left_current is not None:
            start = self._image_to_screen(self._left_start)
            end = self._image_to_screen(self._left_current)
            cv2.rectangle(canvas, start, end, (0, 192, 255), 1)

        mode = "FROZEN" if self.frozen else "LIVE - SPACE TO FREEZE"
        help_text = "L-drag select | wheel zoom | R-drag pan | Z undo | C color | Enter finish | Esc cancel"
        cv2.rectangle(canvas, (0, 0), (canvas_width, 48), (0, 0, 0), -1)
        cv2.putText(
            canvas,
            mode,
            (12, 19),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 255) if not self.frozen else (64, 255, 64),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            help_text,
            (12, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        return canvas

    def _print_last_color(self) -> None:
        if self.frame is None or not self.boxes:
            print("No selected area")
            return
        x1, y1, x2, y2 = self.boxes[-1]
        color = np.rint(self.frame[y1:y2, x1:x2].mean(axis=(0, 1))).astype(int)
        print(f"area={(x1, y1, x2, y2)}, RGB={tuple(color)}")

    def run(self) -> tuple[np.ndarray, list[Box]] | None:
        """Run the editor and return the frozen frame plus selected boxes."""
        cv2.namedWindow(self.WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW_NAME, *self.CANVAS_SIZE)
        cv2.setMouseCallback(self.WINDOW_NAME, self._mouse)
        try:
            while True:
                if self.frame_getter is not None and not self.frozen:
                    self.frame = self.frame_getter().copy()

                cv2.imshow(self.WINDOW_NAME, self._render())
                key = cv2.waitKey(20) & 0xFF
                if key == 255:
                    continue
                if key == 32 and self.frame_getter is not None:
                    if self.boxes:
                        print("A frame with saved selections cannot be resumed")
                    else:
                        self.frozen = not self.frozen
                    continue
                if key in (ord("z"), ord("Z"), 8, 127):
                    if self.boxes:
                        self.boxes.pop()
                    continue
                if key in (ord("c"), ord("C")):
                    self._print_last_color()
                    continue
                if key in (10, 13, ord("s"), ord("S")):
                    if self.frozen and self.frame is not None and self.boxes:
                        return self.frame.copy(), list(self.boxes)
                    continue
                if key in (27, ord("q"), ord("Q")):
                    return None
        finally:
            cv2.destroyWindow(self.WINDOW_NAME)

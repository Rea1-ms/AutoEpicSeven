from __future__ import annotations

import ctypes
import os
import time
import typing as t
from pathlib import Path

import cv2 as _cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

Box = tuple[int, int, int, int]
cv2: t.Any = _cv2


class InteractiveCropper:
    """Small OpenCV editor for selecting multiple boxes from one stable frame."""

    WINDOW_NAME = "AutoEpicSeven Asset Crop"
    CANVAS_SIZE = (1280, 720)
    MIN_ZOOM = 0.25
    MAX_ZOOM = 8.0
    TOOLBAR_HEIGHT = 88

    def __init__(
        self,
        image: np.ndarray | None = None,
        frame_getter: t.Callable[[], np.ndarray] | None = None,
        on_capture: t.Callable[[np.ndarray], str] | None = None,
        on_click: t.Callable[[int, int], None] | None = None,
        on_swipe: t.Callable[[tuple[int, int], tuple[int, int], float], None] | None = None,
        on_back: t.Callable[[], None] | None = None,
        display_scale: float = 1.0,
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
        self.on_capture = on_capture
        self.on_click = on_click
        self.on_swipe = on_swipe
        self.on_back = on_back
        self.status = ""
        self._toolbar_action: str | None = None
        self._gesture_started = 0.0
        self._finish_requested = False
        self._close_requested = False
        self._display_scale = max(0.5, min(1.0, float(display_scale)))
        self._viewport_width = round(self.CANVAS_SIZE[0] * self._display_scale)
        self._viewport_height = round(self.CANVAS_SIZE[1] * self._display_scale)
        self._toolbar_height = round(self.TOOLBAR_HEIGHT * self._display_scale)
        self._font = self._load_font()

        self._left_start: tuple[int, int] | None = None
        self._left_current: tuple[int, int] | None = None
        self._right_start: tuple[int, int] | None = None
        self._right_pan_start: tuple[float, float] | None = None

    @staticmethod
    def _load_font(size=17):
        windows_font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
        for font in (str(windows_font), "msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
            try:
                return ImageFont.truetype(font, max(11, round(size)))
            except OSError:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _set_windows_dpi_awareness() -> None:
        """Make OpenCV mouse coordinates use physical pixels on Windows."""
        if os.name != "nt":
            return
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                pass

    @classmethod
    def _screen_size(cls) -> tuple[int, int] | None:
        if os.name != "nt":
            return None
        try:
            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except (AttributeError, OSError):
            return None

    def _prepare_display(self) -> None:
        """Fit the fixed 1280x720 game frame into a high-DPI screen."""
        self._set_windows_dpi_awareness()
        size = self._screen_size()
        if size is not None:
            screen_width, screen_height = size
            scale = min(
                1.0,
                max(0.5, (screen_width - 32) / self.CANVAS_SIZE[0]),
                max(0.5, (screen_height - 64) /
                    (self.CANVAS_SIZE[1] + self.TOOLBAR_HEIGHT)),
            )
            self._display_scale = scale
        self._viewport_width = round(self.CANVAS_SIZE[0] * self._display_scale)
        self._viewport_height = round(self.CANVAS_SIZE[1] * self._display_scale)
        self._toolbar_height = round(self.TOOLBAR_HEIGHT * self._display_scale)
        self._font = self._load_font(17 * self._display_scale)

    def _buttons(self):
        live = self.frame_getter is not None and not self.frozen
        return (
            ("截图 F3", "capture", self.on_capture is not None),
            ("继续 空格" if self.frozen else "冻结 / 框选 空格", "freeze", self.frame_getter is not None),
            ("返回 B", "back", live and self.on_back is not None),
            ("完成框选 回车", "finish", self.frozen and bool(self.boxes)),
            ("退出 Esc", "close", True),
        )

    def _button_at(self, x: int, y: int) -> str | None:
        button_top = self._viewport_height + round(8 * self._display_scale)
        button_bottom = self._viewport_height + round(42 * self._display_scale)
        if not button_top <= y < button_bottom:
            return None
        for index, (_, action, enabled) in enumerate(self._buttons()):
            left = round((12 + index * 190) * self._display_scale)
            right = round((192 + index * 190) * self._display_scale)
            if enabled and left <= x < right:
                return action
        return None

    def _action(self, action: str) -> None:
        if action == "capture" and self.on_capture is not None and self.frame is not None:
            # Save the displayed source frame, never the zoomed canvas or its
            # toolbar/selection overlays. Freezing must preserve this exact frame.
            path = self.on_capture(self.frame.copy())
            self.status = f"已保存：{Path(path).name}"
        elif action == "freeze" and self.frame_getter is not None:
            if self.boxes:
                self.status = "请先撤销选区，再继续实时预览"
            else:
                self.frozen = not self.frozen
                self._left_start = self._left_current = None
                self.status = ""
        elif action == "back" and not self.frozen and self.on_back is not None:
            self.on_back()
        elif action == "finish" and self.frozen and self.boxes:
            self._finish_requested = True
        elif action == "close":
            self._close_requested = True

    def _device_point(self, x: int, y: int) -> tuple[int, int] | None:
        if self.frame is None or not (0 <= x < self._viewport_width and 0 <= y < self._viewport_height):
            return None
        image_x = (x / self._display_scale - self.pan_x) / self.zoom
        image_y = (y / self._display_scale - self.pan_y) / self.zoom
        height, width = self.frame.shape[:2]
        if 0 <= image_x < width and 0 <= image_y < height:
            return min(width - 1, round(image_x)), min(height - 1, round(image_y))
        return None

    @staticmethod
    def _wheel_delta(flags: int) -> int:
        value = (flags >> 16) & 0xFFFF
        return value - 0x10000 if value & 0x8000 else value

    def _screen_to_image(self, point: tuple[int, int]) -> tuple[int, int]:
        if self.frame is None:
            return 0, 0
        height, width = self.frame.shape[:2]
        x = round((point[0] / self._display_scale - self.pan_x) / self.zoom)
        y = round((point[1] / self._display_scale - self.pan_y) / self.zoom)
        return max(0, min(width, x)), max(0, min(height, y))

    def _image_to_screen(self, point: tuple[int, int]) -> tuple[int, int]:
        return (
            round((point[0] * self.zoom + self.pan_x) * self._display_scale),
            round((point[1] * self.zoom + self.pan_y) * self._display_scale),
        )

    def _zoom_at(self, point: tuple[int, int], factor: float) -> None:
        image_x = (point[0] / self._display_scale - self.pan_x) / self.zoom
        image_y = (point[1] / self._display_scale - self.pan_y) / self.zoom
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self.zoom * factor))
        self.pan_x = point[0] / self._display_scale - image_x * new_zoom
        self.pan_y = point[1] / self._display_scale - image_y * new_zoom
        self.zoom = new_zoom

    def _mouse(self, event: int, x: int, y: int, flags: int, _param) -> None:
        # A press must start and end on the same toolbar button. Toolbar and
        # blank viewport pixels must never be clamped into a device tap.
        if event == cv2.EVENT_LBUTTONDOWN and y >= self._viewport_height:
            self._left_start = self._left_current = None
            self._toolbar_action = self._button_at(x, y)
            return
        if event == cv2.EVENT_LBUTTONUP and self._toolbar_action is not None:
            action, self._toolbar_action = self._toolbar_action, None
            if self._button_at(x, y) == action:
                self._action(action)
            return
        if y >= self._viewport_height:
            if event == cv2.EVENT_LBUTTONUP:
                self._left_start = self._left_current = None
            if event == cv2.EVENT_RBUTTONUP:
                self._right_start = self._right_pan_start = None
            return
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
            if event == cv2.EVENT_LBUTTONDOWN:
                self._left_start = self._device_point(x, y)
                self._gesture_started = time.monotonic()
            elif event == cv2.EVENT_LBUTTONUP and self._left_start is not None:
                start, self._left_start = self._left_start, None
                end = self._device_point(x, y)
                if end is None:
                    return
                if np.linalg.norm(np.subtract(end, start)) >= 8:
                    if self.on_swipe is not None:
                        self.on_swipe(start, end, min(2.0, max(0.05, time.monotonic() - self._gesture_started)))
                elif self.on_click is not None:
                    self.on_click(*end)
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
        canvas_width, canvas_height = self._viewport_width, self._viewport_height
        canvas = np.zeros((canvas_height + self._toolbar_height, canvas_width, 3), dtype=np.uint8)
        if self.frame is None:
            return canvas

        frame_bgr = cv2.cvtColor(self.frame, cv2.COLOR_RGB2BGR)
        scaled = cv2.resize(
            frame_bgr,
            dsize=None,
            fx=self.zoom * self._display_scale,
            fy=self.zoom * self._display_scale,
            interpolation=cv2.INTER_NEAREST if self.zoom >= 1 else cv2.INTER_AREA,
        )
        left = round(self.pan_x * self._display_scale)
        top = round(self.pan_y * self._display_scale)
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

        toolbar = Image.new("RGB", (canvas_width, self._toolbar_height), (25, 28, 34))
        draw = ImageDraw.Draw(toolbar)
        for index, (label, _, enabled) in enumerate(self._buttons()):
            left = round((12 + index * 190) * self._display_scale)
            draw.rounded_rectangle((left, round(8 * self._display_scale),
                                    left + round(180 * self._display_scale),
                                    round(42 * self._display_scale)),
                                   radius=max(2, round(5 * self._display_scale)),
                                   fill=(53, 65, 81) if enabled else (35, 39, 46))
            draw.text((left + round(12 * self._display_scale), round(13 * self._display_scale)), label, font=self._font,
                      fill=(240, 243, 247) if enabled else (113, 122, 134))
        mode = "已冻结：左键拖动框选" if self.frozen else "实时：左键点击 / 拖动操控"
        help_text = f"{mode} | 滚轮缩放 / 右键平移 | Z 撤销 / C 颜色"
        draw.text((round(12 * self._display_scale), round(54 * self._display_scale)),
                  self.status or help_text, font=self._font, fill=(212, 220, 231))
        canvas[canvas_height:] = np.asarray(toolbar)[:, :, ::-1]
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
        self._prepare_display()
        cv2.namedWindow(self.WINDOW_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW_NAME, self._viewport_width, self._viewport_height + self._toolbar_height)
        cv2.setMouseCallback(self.WINDOW_NAME, self._mouse)
        try:
            while True:
                if self.frame_getter is not None and not self.frozen:
                    self.frame = self.frame_getter().copy()

                cv2.imshow(self.WINDOW_NAME, self._render())
                key = cv2.waitKeyEx(20)
                if cv2.getWindowProperty(self.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                    return None
                if self._close_requested:
                    return None
                if self._finish_requested:
                    return self.frame.copy(), list(self.boxes)
                if key == -1:
                    continue
                if key in (0x720000, 0xFFC0, 16777266) or (
                    self.on_capture is not None and key in (ord("s"), ord("S"))
                ):
                    self._action("capture")
                    continue
                if key in (ord("b"), ord("B")):
                    self._action("back")
                    continue
                if key == 32 and self.frame_getter is not None:
                    self._action("freeze")
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

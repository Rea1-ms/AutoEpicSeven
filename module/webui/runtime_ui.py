"""Session-wide progress and opt-in operating-system notifications."""

from pathlib import Path
from uuid import uuid4

from pywebio.exceptions import SessionClosedException, SessionNotFoundException
from pywebio.output import put_button, put_html, put_text, toast
from pywebio.session import defer_call, eval_js, run_js

from module.logger import logger
from module.runtime_events import runtime_events
from module.webui import lang
from module.webui.setting import State


def runtime_text(zh, en):
    return zh if lang.LANG.startswith("zh") else en


def put_system_notification_settings(gui, task):
    """Reuse the existing config save path; never request permission server-side."""
    put_text(runtime_text(
        "系统通知可与远程推送同时启用。桌面版可直接通知；浏览器需在下方授权，并保持网页打开。",
        "System notifications can be enabled alongside remote delivery. Desktop notifications work directly; "
        "enable browser notifications below and keep this page open.",
    )).style("font-size: .85rem; opacity: .8")
    put_html('<div data-aes-notification-control></div>')
    put_button(runtime_text('发送测试通知', 'Send test notification'),
               onclick=lambda: gui.runtime_ui.send_test_notification(), color='secondary')


class RuntimeUI:
    """A persistent UI task, deliberately not removed when the user changes pages."""

    def __init__(self, gui):
        self.gui = gui
        self.client_id = uuid4().hex
        self.selected_config = ''
        self.closed = False
        self.failed = False
        self.registered = False

    def start(self):
        source = Path(__file__).with_name("assets") / "js" / "runtime.js"
        run_js(source.read_text(encoding="utf-8"), runtimeOptions={
            "electron": State.electron,
            "chinese": lang.LANG.startswith("zh"),
        })
        defer_call(self.stop)
        self.gui.task_handler.add(self.poll, 2, pending_delete=False)

    def stop(self):
        self.closed = True
        runtime_events.unregister_client(self.client_id)
        self.registered = False

    def _refresh_client(self):
        kind = eval_js("window.aesRuntime ? window.aesRuntime.clientKind() : null")
        if self.closed:
            return False
        if kind in ("electron", "browser"):
            self.registered = bool(runtime_events.register_client(self.client_id, kind=kind))
        elif self.registered:
            runtime_events.unregister_client(self.client_id)
            self.registered = False
        return self.registered

    def send_test_notification(self):
        """Refresh permission immediately, without waiting for the next heartbeat."""
        try:
            if not self._refresh_client():
                toast(runtime_text("当前窗口尚未启用系统通知。", "System notifications are not enabled in this window."),
                      color="warning")
                return False
            queued = runtime_events.notify("AES", runtime_text(
                "这是一条系统通知测试。", "This is a system notification test.",
            ))
            toast(runtime_text("测试通知已排队，实际显示由系统通知设置决定。",
                               "Test notification queued; display depends on system notification settings.")
                  if queued else runtime_text("当前没有可用的通知连接。", "No notification receiver is available."),
                  color="success" if queued else "warning")
            return queued
        except (SessionClosedException, SessionNotFoundException):
            self.stop()
            return False

    def poll(self):
        if self.closed or not self.gui.alive:
            return
        try:
            # Asking the live page every time also notices permission revocation
            # and an expired Electron handshake. A cached grant would otherwise
            # consume notifications after its actual receiver disappeared.
            self._refresh_client()
            if self.closed:
                return

            if self.gui.alas_name:
                self.selected_config = self.gui.alas_name
            progress = runtime_events.get_progress(self.selected_config)
            if progress is None:
                progress = {"config_name": self.selected_config, "state": "stopped",
                            "completed": 0, "total": None, "task": ""}
            else:
                progress = dict(progress)
            task = progress.get("task", "")
            progress["task_label"] = lang.dic_lang.get(lang.LANG, {}).get(
                f"Task.{task}.name", task,
            )
            notifications = runtime_events.poll_notifications(self.client_id) if self.registered else []
            # Progress doubles as a host heartbeat, including when unchanged.
            run_js("window.aesRuntime && window.aesRuntime.update(progress, notifications)",
                   progress=progress, notifications=notifications)
            self.failed = False
        except (SessionClosedException, SessionNotFoundException):
            self.stop()
        except Exception:  # noqa: BLE001 - Optional UI delivery must not stop other session tasks.
            runtime_events.unregister_client(self.client_id)
            self.registered = False
            if not self.failed:
                logger.warning("Runtime UI unavailable; automation will continue")
            self.failed = True

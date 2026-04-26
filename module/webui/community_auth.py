from __future__ import annotations

import os
import time
from datetime import datetime
from json import dumps
from typing import Any

from pywebio.output import (
    put_buttons,
    put_html,
    put_scope,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.session import run_js

from module.config.deep import deep_get
from module.config.server import is_cn_server
from module.webui.lang import t


COMMUNITY_AIO_TASK = "CommunityAio"
COMMUNITY_AUTH_TASK = "CommunityAuth"
COMMUNITY_AUTH_TASK_SCOPE = "group_community_auth_credentials"
WEBUI_IPC_REQUEST_SOURCE = "aes-webui"
WEBUI_IPC_RESPONSE_SOURCE = "aes-electron-bridge"
WEBUI_IPC_INVOKE = "ipc-invoke"
WEBUI_IPC_RESPONSE = "ipc-response"


def is_community_visible(gui: Any) -> bool:
    """
    Decide whether current selected instance should expose CN community UI.

    Important: this reads real config content instead of relying on the
    instance name, so renamed CN configs still expose Community AIO controls.
    """
    if not gui.alas_name:
        return False
    config = gui.alas_config.read_file(gui.alas_name)
    package_name = deep_get(config, "Alas.Emulator.PackageName", default="")
    if not package_name:
        return False
    return is_cn_server(package_name)


def format_remaining_time(seconds: int) -> str:
    minutes, _ = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def get_community_credentials_status(config: dict, config_name: str = "") -> dict:
    from community.aio import (
        get_default_credentials_path,
        get_token_expiry,
        load_credentials_file,
    )

    configured_path = str(
        deep_get(config, "CommunityAuth.CommunityAuth.CredentialsFile", default="")
        or deep_get(config, "CommunityAio.CommunityAio.CredentialsFile", default="")
        or ""
    ).strip()
    credentials_path = configured_path or get_default_credentials_path(config_name)
    status = {
        "path": credentials_path,
        "ok": False,
        "summary": "",
        "detail": "",
    }

    if not os.path.exists(credentials_path):
        status["summary"] = "未找到 CK 文件，请先点击“更新 CK”完成登录。"
        return status

    try:
        creds = load_credentials_file(credentials_path)
    except ValueError as exc:
        status["summary"] = f"CK 文件读取失败：{exc}"
        return status

    missing = [key for key in ("token", "pd_did", "pd_dvid") if not creds.get(key)]
    if missing:
        status["summary"] = f"CK 不完整：缺少 {', '.join(missing)}"
        return status

    expiry = get_token_expiry(creds["token"])
    if expiry is None:
        status["ok"] = True
        status["summary"] = "CK 已就绪（token 未携带 exp 字段）"
        return status

    now = int(time.time())
    expiry_text = datetime.fromtimestamp(expiry).strftime("%Y-%m-%d %H:%M:%S")
    if expiry <= now:
        status["summary"] = f"CK 已过期（过期时间：{expiry_text}）"
        return status

    status["ok"] = True
    status["summary"] = f"CK 有效，剩余约 {format_remaining_time(expiry - now)}"
    status["detail"] = f"过期时间：{expiry_text}"
    return status


def _electron_invoke_js(
    channel: str,
    alert_message: str,
    console_label: str,
    ipc_args: list | None = None,
) -> str:
    script = """
        (async () => {
          const channel = __CHANNEL__;
          const ipcArgs = __IPC_ARGS__;
          const alertMessage = __ALERT_MESSAGE__;
          const consoleLabel = __CONSOLE_LABEL__;
          const directInvoke = window.__electron_preload__ipcRendererInvoke;
          if (typeof directInvoke === 'function') {
            try {
              await directInvoke(channel, ...ipcArgs);
              return;
            } catch (error) {
              console.error(consoleLabel, error);
              window.alert(alertMessage);
              return;
            }
          }

          if (!window.parent || window.parent === window) {
            window.alert(alertMessage);
            return;
          }

          const requestId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
          try {
            await new Promise((resolve, reject) => {
              const timer = window.setTimeout(() => {
                window.removeEventListener('message', onMessage);
                reject(new Error('Electron IPC bridge timed out'));
              }, 5000);

              function onMessage(event) {
                const data = event.data;
                if (!data || data.source !== __RESPONSE_SOURCE__) return;
                if (data.type !== __RESPONSE_TYPE__ || data.requestId !== requestId) return;
                window.clearTimeout(timer);
                window.removeEventListener('message', onMessage);
                if (data.ok) {
                  resolve(data.result);
                } else {
                  reject(new Error(data.error || 'Electron IPC bridge failed'));
                }
              }

              window.addEventListener('message', onMessage);
              window.parent.postMessage({
                source: __REQUEST_SOURCE__,
                type: __REQUEST_TYPE__,
                requestId,
                channel,
                args: ipcArgs,
              }, '*');
            });
          } catch (error) {
            console.error(consoleLabel, error);
            window.alert(`${alertMessage}\n\n${error && error.message ? error.message : error}`);
          }
        })();
        """
    return (
        script
        .replace("__CHANNEL__", dumps(channel))
        .replace("__IPC_ARGS__", dumps(ipc_args or []))
        .replace("__ALERT_MESSAGE__", dumps(alert_message))
        .replace("__CONSOLE_LABEL__", dumps(console_label))
        .replace("__RESPONSE_SOURCE__", dumps(WEBUI_IPC_RESPONSE_SOURCE))
        .replace("__RESPONSE_TYPE__", dumps(WEBUI_IPC_RESPONSE))
        .replace("__REQUEST_SOURCE__", dumps(WEBUI_IPC_REQUEST_SOURCE))
        .replace("__REQUEST_TYPE__", dumps(WEBUI_IPC_INVOKE))
    )


def open_community_login_window(config_name: str = "") -> None:
    run_js(
        _electron_invoke_js(
            "e7:open-login",
            "当前运行环境不支持 Electron 登录拉起，请在桌面端使用。",
            "[CommunityAuth] open-login failed",
            ipc_args=[config_name] if config_name else [],
        )
    )
    toast(
        "已尝试拉起登录窗口。登录完成后可查看工具日志或刷新 CK 状态。",
        color="info",
        duration=3,
        position="right",
    )


def close_community_login_window() -> None:
    run_js(
        _electron_invoke_js(
            "e7:close-login",
            "当前运行环境不支持 Electron 登录窗口控制，请在桌面端使用。",
            "[CommunityAuth] close-login failed",
        )
    )
    toast(
        "已尝试关闭登录窗口。",
        color="info",
        duration=3,
        position="right",
    )


def start_community_auth_tool(gui: Any, task: str, default_start) -> None:
    default_start()
    open_community_login_window(config_name=gui.alas_name)


def stop_community_auth_tool(gui: Any, task: str, default_stop) -> None:
    close_community_login_window()
    default_stop()


def render_community_credentials_panel(gui: Any, scope: str) -> None:
    config = gui.alas_config.read_file(gui.alas_name)
    status = get_community_credentials_status(config, config_name=gui.alas_name)

    def refresh_panel() -> None:
        render_community_credentials_panel(gui, scope)

    with use_scope(scope, clear=True):
        put_text(t("Gui.Text.CommunityAuthCredentials"))
        put_text(t("Gui.Text.CommunityAuthCredentialsHelp"))
        put_html('<hr class="hr-group">')
        if status["ok"]:
            put_text(status["summary"]).style("color: var(--bs-success);")
        else:
            put_warning(status["summary"])
        if status["detail"]:
            put_text(status["detail"]).style("--arg-help--")
        put_buttons(
            buttons=[
                {"label": "刷新 CK 状态", "value": "refresh-ck", "color": "off"},
            ],
            onclick=[refresh_panel],
        )


def render_community_auth_task_panel(gui: Any) -> None:
    put_scope(COMMUNITY_AUTH_TASK_SCOPE, scope="groups")
    render_community_credentials_panel(gui, COMMUNITY_AUTH_TASK_SCOPE)

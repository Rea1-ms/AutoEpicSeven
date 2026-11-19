"""Tower profile controls contributed through the existing task-detail hook."""

from dataclasses import replace

from pywebio.input import file_upload
from pywebio.output import (
    clear,
    close_popup,
    popup,
    put_buttons,
    put_file,
    put_scope,
    put_text,
    put_warning,
    toast,
    use_scope,
)
from pywebio.pin import pin, pin_on_change, put_textarea

from module.webui.pin import put_checkbox, put_input, put_select
from tasks.automaton_tower.policy import MAX_PROFILE_BYTES, ProfileError
from tasks.automaton_tower.profile_store import ProfileStore, StaminaSettings

SCOPE = "group_automaton_tower_profile"
SELECT = "automaton_tower_profile_choice"
EDITOR = "automaton_tower_profile_editor"
DEBUG = "automaton_tower_debug_capture"
REFILL = "automaton_tower_stamina_refill"
RESERVE = "automaton_tower_stamina_reserve"
RESERVE_SCOPE = "group_automaton_tower_stamina_reserve"
LEGACY = "legacy"


def render_automaton_tower_panel(gui):
    put_scope(SCOPE, scope="groups")
    render_profile_controls(gui)


def render_profile_controls(gui):
    store = ProfileStore()
    account = gui.alas_name

    def report_error(exc):
        toast(str(exc), color="error", duration=6)

    def selected_id():
        value = pin[SELECT]
        return None if value == LEGACY else value

    def save_debug(value):
        try:
            store.set_debug_enabled(account, "enabled" in (value or []))
        except (ProfileError, OSError) as exc:
            report_error(exc)
            return
        toast("调试设置已保存。", duration=1, position="right", color="success")

    def save_stamina(**changes) -> StaminaSettings | None:
        try:
            settings = replace(store.stamina(account), **changes)
            store.set_stamina(account, settings)
        except (ProfileError, OSError) as exc:
            report_error(exc)
            return None
        if "enabled" in changes:
            toast("行动力设置已保存。", duration=1, position="right", color="success")
        return settings

    def save_refill(value):
        settings = save_stamina(enabled="enabled" in (value or []))
        if settings is not None:
            render_reserve(settings)

    def save_reserve(value):
        if value is not None:
            save_stamina(reserve_leaves=value)

    def render_reserve(settings: StaminaSettings):
        clear(RESERVE_SCOPE)
        if not settings.enabled:
            return
        put_input(
            RESERVE, type="number", label="至少保留的叶子数量", value=settings.reserve_leaves,
            min=0, step=1, help_text="仅保存非负整数；留空或填写无效时保留上次设置。",
            scope=RESERVE_SCOPE,
        )
        pin_on_change(RESERVE, save_reserve, clear=True, serial_mode=True)

    def activate():
        try:
            store.activate(account, selected_id())
        except (ProfileError, OSError) as exc:
            report_error(exc)
            return
        toast("已保存，下次启动月塔时使用。")
        render_profile_controls(gui)

    def import_file():
        upload = file_upload(
            "导入月塔装置方案",
            accept=".yaml,.yml",
            max_size=MAX_PROFILE_BYTES,
            required=False,
        )
        if upload is None:
            return
        try:
            profile_id = store.import_profile(upload["content"])
            store.activate(account, profile_id)
        except (ProfileError, OSError) as exc:
            report_error(exc)
            return
        render_profile_controls(gui)

    def export_file():
        try:
            profile_id = selected_id()
            if profile_id is None:
                raise ProfileError("请先选择一个装置方案")
            content = store.export(profile_id)
        except (ProfileError, OSError) as exc:
            report_error(exc)
            return
        with popup("导出装置方案"):
            put_file("月塔装置方案.yaml", content, label="下载方案")

    def edit_copy():
        try:
            content = store.export(selected_id() or "builtin:thief_back").decode(
                "utf-8-sig"
            )
        except (ProfileError, OSError) as exc:
            report_error(exc)
            return

        def save_copy():
            try:
                profile_id = store.import_profile(pin[EDITOR])
                store.activate(account, profile_id)
            except (ProfileError, OSError) as exc:
                report_error(exc)
                return
            close_popup()
            render_profile_controls(gui)

        with popup("编辑后另存为新方案", size="large"):
            put_text("填写实际队伍和优先顺序；保存会新增副本，保留原方案。")
            put_textarea(EDITOR, value=content, rows=20)
            put_buttons(["保存并使用", "取消"], onclick=[save_copy, close_popup])

    with use_scope(SCOPE, clear=True):
        put_text("装置选择方案")
        put_text(
            "方案按账号分别选择，启动时读取；不改变游戏配队。未启用方案时沿用原第一张策略。"
        )
        put_text(
            f"当前资料库收录{len(store.catalog.by_id)}种装置，遇到未登记名称会保存截图并停止，便于补充资料。"
        )
        profiles = store.list_profiles()
        active = None
        try:
            active = store.active_id(account)
            if active is not None:
                store.load(active)
        except (ProfileError, OSError) as exc:
            put_warning(str(exc))
        options = [{"label": "原第一张策略", "value": LEGACY}]
        for profile in profiles:
            label = ("内置示例：" if profile.builtin else "用户方案：") + profile.name
            if profile.error:
                put_warning(profile.error)
                label = "格式错误：" + profile.name
            options.append(
                {
                    "label": label + " · " + profile.id.split(":", 1)[1][:6],
                    "value": profile.id,
                }
            )
        if active is not None and active not in {option["value"] for option in options}:
            options.append({"label": "当前方案无法读取", "value": active})
        put_select(SELECT, options=options, value=active or LEGACY)
        put_buttons(
            ["使用所选方案", "导入方案", "导出所选", "复制并编辑"],
            onclick=[activate, import_file, export_file, edit_copy],
        )
        enabled = False
        try:
            enabled = store.debug_enabled(account)
        except (ProfileError, OSError) as exc:
            put_warning(str(exc))
        put_checkbox(
            DEBUG,
            options=[{"label": "记录装置调试截图", "value": "enabled"}],
            value=["enabled"] if enabled else [],
        )
        pin_on_change(DEBUG, save_debug, clear=True, serial_mode=True)
        put_text(
            "勾选后自动保存，下次启动生效。每轮保存三选一和确认前的完整画面，附楼层、选择方式和结果。"
            "原第一张策略也能采集未知装置；启用方案时仍按方案规则判断。"
        )
        put_text(
            "截图保存在 screenshots/automaton_tower_debug（调试截图目录），启动日志显示本次路径。"
        )
        stamina = StaminaSettings()
        try:
            stamina = store.stamina(account)
        except (ProfileError, OSError) as exc:
            put_warning(str(exc))
        put_text("月塔行动力（按账号保存）")
        put_text("自动补充仅支持国际服简中，默认关闭。仅用叶子，每片补充 80 点，不会改用钻石。")
        put_text("修改后自动保存，下次启动生效。")
        put_checkbox(
            REFILL,
            options=[{"label": "行动力不足时用叶子补充", "value": "enabled"}],
            value=["enabled"] if stamina.enabled else [],
        )
        put_scope(RESERVE_SCOPE)
        render_reserve(stamina)
        pin_on_change(REFILL, save_refill, clear=True, serial_mode=True)

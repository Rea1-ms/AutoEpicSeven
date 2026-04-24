from __future__ import annotations

from typing import Any, Callable

from module.webui.community_auth import (
    COMMUNITY_AIO_TASK,
    COMMUNITY_AUTH_TASK,
    is_community_visible,
    render_community_auth_task_panel,
    start_community_auth_tool,
    stop_community_auth_tool,
)


TaskVisibility = Callable[[Any], bool]
TaskRenderer = Callable[[Any], None]
TaskDefaultAction = Callable[[], None]
TaskAction = Callable[[Any, str, TaskDefaultAction], None]


TASK_VISIBILITY_FILTERS: dict[str, TaskVisibility] = {
    COMMUNITY_AIO_TASK: is_community_visible,
    COMMUNITY_AUTH_TASK: is_community_visible,
}

TASK_DETAIL_RENDERERS: dict[str, TaskRenderer] = {
    COMMUNITY_AUTH_TASK: render_community_auth_task_panel,
}

TASK_START_HANDLERS: dict[str, TaskAction] = {
    COMMUNITY_AUTH_TASK: start_community_auth_tool,
}

TASK_STOP_HANDLERS: dict[str, TaskAction] = {
    COMMUNITY_AUTH_TASK: stop_community_auth_tool,
}


def is_task_visible(gui: Any, task: str) -> bool:
    """
    Return whether a task should be visible for the current WebUI instance.

    Task-specific conditions live in extension modules so ``AlasGUI`` does not
    need to inherit or know about every feature-specific WebUI panel.
    """
    visibility = TASK_VISIBILITY_FILTERS.get(task)
    if visibility is None:
        return True
    return visibility(gui)


def render_task_detail(gui: Any, task: str) -> None:
    """
    Render optional task-specific controls in the task detail page.

    This mirrors SRC's configuration-driven tool page idea: ``app.py`` owns the
    generic page layout, while feature modules only contribute their own scoped
    controls when explicitly registered here.
    """
    renderer = TASK_DETAIL_RENDERERS.get(task)
    if renderer is not None:
        renderer(gui)


def get_task_start(gui: Any, task: str, default_start: TaskDefaultAction) -> TaskDefaultAction:
    handler = TASK_START_HANDLERS.get(task)
    if handler is None:
        return default_start
    return lambda: handler(gui, task, default_start)


def get_task_stop(gui: Any, task: str, default_stop: TaskDefaultAction) -> TaskDefaultAction:
    handler = TASK_STOP_HANDLERS.get(task)
    if handler is None:
        return default_stop
    return lambda: handler(gui, task, default_stop)

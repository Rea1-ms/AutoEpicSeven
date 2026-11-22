"""Shareable profiles separate from account selection and observed run records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from deploy.Windows.atomic import atomic_write
from tasks.automaton_tower.policy import (
    DeviceCatalog,
    Profile,
    ProfileError,
    parse_profile,
)

ROOT = Path(__file__).resolve().parents[2]


def safe_component(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[\w-]{1,80}", value, re.UNICODE
    ):
        raise ProfileError("配置标识包含不支持的路径字符")
    if value.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        raise ProfileError("配置标识不能使用系统保留名称")
    return value


@dataclass(frozen=True)
class SavedProfile:
    id: str
    name: str
    builtin: bool
    error: str | None = None


@dataclass(frozen=True)
class StaminaSettings:
    enabled: bool = False
    reserve_leaves: int = 0

    def __post_init__(self):
        if (
            type(self.enabled) is not bool
            or type(self.reserve_leaves) is not int
            or self.reserve_leaves < 0
        ):
            raise ProfileError("保留叶子数量必须是非负整数，自动补充必须启用或关闭")


class ProfileStore:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.builtin = root / "tasks/automaton_tower/profiles"
        self.user = root / "config/automaton_tower/profiles"
        self.instances = root / "config/automaton_tower/instances"
        self.debug_settings = root / "config/automaton_tower/debug"
        self.stamina_settings = root / "config/automaton_tower/stamina"
        self.catalog = DeviceCatalog()

    def stamina(self, account: str) -> StaminaSettings:
        path = self.stamina_settings / (safe_component(account) + ".json")
        if not path.exists():
            return StaminaSettings()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != {"enabled", "reserve_leaves"}:
                raise ValueError("invalid stamina settings")
            return StaminaSettings(**data)
        except (OSError, ValueError) as exc:
            raise ProfileError("无法读取月塔行动力设置") from exc

    def set_stamina(self, account: str, settings: StaminaSettings):
        path = self.stamina_settings / (safe_component(account) + ".json")
        self.stamina_settings.mkdir(parents=True, exist_ok=True)
        atomic_write(
            str(path),
            json.dumps({"enabled": settings.enabled, "reserve_leaves": settings.reserve_leaves}) + "\n",
        )

    def debug_enabled(self, account: str) -> bool:
        path = self.debug_settings / (safe_component(account) + ".json")
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProfileError("无法读取月塔调试设置") from exc
        if (
            not isinstance(data, dict)
            or set(data) != {"enabled"}
            or type(data["enabled"]) is not bool
        ):
            raise ProfileError("月塔调试设置格式不正确")
        return data["enabled"]

    def set_debug_enabled(self, account: str, enabled: bool):
        if type(enabled) is not bool:
            raise ProfileError("调试开关需要启用或关闭")
        path = self.debug_settings / (safe_component(account) + ".json")
        self.debug_settings.mkdir(parents=True, exist_ok=True)
        atomic_write(str(path), json.dumps({"enabled": enabled}) + "\n")

    def _profile_path(self, profile_id: str) -> Path:
        if not isinstance(profile_id, str) or ":" not in profile_id:
            raise ProfileError("方案标识无效")
        kind, name = profile_id.split(":", 1)
        if kind not in ("builtin", "user"):
            raise ProfileError("方案来源无效")
        return (self.builtin if kind == "builtin" else self.user) / (
            safe_component(name) + ".yaml"
        )

    def export(self, profile_id: str) -> bytes:
        try:
            content = self._profile_path(profile_id).read_bytes()
        except OSError as exc:
            raise ProfileError(f"无法读取所选方案：{profile_id}") from exc
        parse_profile(content, self.catalog)
        return content

    def load(self, profile_id: str) -> Profile:
        return parse_profile(self.export(profile_id), self.catalog)

    def list_profiles(self) -> list[SavedProfile]:
        result = []
        for kind, folder in (("builtin", self.builtin), ("user", self.user)):
            for path in sorted(folder.glob("*.yaml")):
                profile_id = kind + ":" + path.stem
                try:
                    profile = self.load(profile_id)
                except (ProfileError, OSError) as exc:
                    result.append(
                        SavedProfile(profile_id, path.stem, kind == "builtin", str(exc))
                    )
                else:
                    result.append(
                        SavedProfile(profile_id, profile.name, kind == "builtin")
                    )
        return result

    def import_profile(self, content: bytes | str) -> str:
        parse_profile(content, self.catalog)
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.user.mkdir(parents=True, exist_ok=True)
        name = uuid4().hex
        # Shared display names must never overwrite an earlier user profile.
        with (self.user / (name + ".yaml")).open(
            "x", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(text)
        return "user:" + name

    def active_id(self, account: str) -> str | None:
        path = self.instances / (safe_component(account) + ".json")
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProfileError("无法读取月塔方案选择记录") from exc
        if not isinstance(data, dict) or set(data) != {"profile"}:
            raise ProfileError("月塔方案选择记录格式不正确")
        profile_id = data["profile"]
        if profile_id is not None:
            self._profile_path(profile_id)
        return profile_id

    def active(self, account: str) -> Profile | None:
        profile_id = self.active_id(account)
        return self.load(profile_id) if profile_id is not None else None

    def activate(self, account: str, profile_id: str | None) -> None:
        path = self.instances / (safe_component(account) + ".json")
        if profile_id is not None:
            self.load(profile_id)
        self.instances.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"profile": profile_id}, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

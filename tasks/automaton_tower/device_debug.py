"""Optional full-frame evidence, independent of device recognition and choice."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from module.base.utils import save_image
from module.logger import logger
from tasks.automaton_tower.profile_store import ROOT, safe_component


class DeviceDebug:
    def __init__(self, account: str, server: str, root: Path = ROOT):
        self.path = (
            root
            / "screenshots/automaton_tower_debug"
            / safe_component(account)
            / safe_component(server)
            / uuid4().hex
        )
        self.sequence = 0
        self.entry = None
        self.enabled = True

    def _save(self, image=None, stage=None):
        if not self.enabled or self.entry is None:
            return
        try:
            folder = self.path / f"{self.sequence:03d}"
            folder.mkdir(parents=True, exist_ok=True)
            if stage is not None:
                filename = stage + ".png"
                save_image(image, str(folder / filename))
                self.entry["images"][stage] = filename
            (folder / "record.json").write_text(
                json.dumps(self.entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        except OSError as exc:
            # Evidence is optional. Disk failure must not change the selected
            # card, retry timing, or the game's state; warn once and stop saving.
            self.enabled = False
            logger.warning(f"Automaton Tower: debug capture disabled: {exc}")

    def begin(self, image, floor: int | None, mode: str):
        if not self.enabled:
            return
        self.sequence += 1
        self.entry = {
            "version": 1,
            "server": self.path.parent.name,
            "floor": floor,
            "mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "images": {},
            "candidates": None,
            "chosen_slot": None,
            "reason": None,
            "confirmation_requests": 0,
            "status": "observed",
            "destination": None,
            "error": None,
        }
        self._save(image, "offer")

    def decision(self, candidates, choice):
        if self.entry is None:
            return
        self.entry["candidates"] = [
            {
                "slot": c.slot,
                "id": c.device.id,
                "name": c.device.name,
                "level": c.level,
                "max_level": c.max_level,
            }
            for c in candidates
        ]
        self.entry["chosen_slot"] = choice.candidate.slot
        self.entry["reason"] = choice.reason
        self._save()

    def requested(self, image):
        if self.entry is None:
            return
        self.entry["confirmation_requests"] += 1
        self.entry["status"] = "confirmation_requested"
        # Retries retain the first pre-confirmation frame. A click does not
        # establish a new reward round or prove that the device was obtained.
        stage = None if "selected" in self.entry["images"] else "selected"
        self._save(image, stage)

    def finish(self, destination: str, *, confirmed=False, error=None):
        if self.entry is None:
            return
        requested = self.entry["confirmation_requests"] > 0
        self.entry["status"] = "confirmed" if confirmed and requested else "interrupted"
        self.entry["destination"] = destination
        self.entry["error"] = error
        self._save()

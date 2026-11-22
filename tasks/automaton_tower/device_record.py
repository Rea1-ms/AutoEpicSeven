"""A partial run journal; confirmed choices never imply a complete backpack."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from tasks.automaton_tower.policy import Candidate, Choice
from tasks.automaton_tower.profile_store import ROOT, safe_component


class DeviceRecord:
    def __init__(self, account: str, server: str, profile_name: str, root: Path = ROOT):
        self.path = (
            root
            / "data/automaton_tower"
            / safe_component(account)
            / (uuid4().hex + ".json")
        )
        self.data = {
            "version": 1,
            "server": server,
            "profile": profile_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "inventory_complete": False,
            "period": None,
            "difficulty": None,
            "choices": [],
        }
        self.pending = None

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def begin(
        self, candidates: tuple[Candidate, ...], choice: Choice, floor: int | None
    ):
        if self.pending is not None:
            raise ValueError("A previous device request is still pending")
        entry = {
            "floor": floor,
            "candidates": [
                {
                    "id": c.device.id,
                    "name": c.device.name,
                    "level": c.level,
                    "max_level": c.max_level,
                }
                for c in candidates
            ],
            "slot": choice.candidate.slot,
            "reason": choice.reason,
            "status": "selection_pending",
            "destination": None,
        }
        self.data["choices"].append(entry)
        self.pending = entry
        self._save()

    def requested(self):
        if self.pending is None:
            raise ValueError("No device choice can be confirmed")
        self.pending["status"] = "confirmation_requested"
        self._save()

    def confirmed(self, destination: str):
        if self.pending is None or self.pending["status"] != "confirmation_requested":
            return False
        self.pending["status"] = "confirmed"
        self.pending["destination"] = destination
        self.pending = None
        self._save()
        return True

    def abandon(self):
        if self.pending is not None:
            self.pending["status"] = "interrupted"
            self.pending = None
            self._save()

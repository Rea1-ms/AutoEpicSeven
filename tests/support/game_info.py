"""Synthetic game-info documents shared by rule tests."""
import json
from datetime import datetime
from module.game_info import catalog

NOW = datetime.fromisoformat("2030-01-22T11:00:00+08:00")

def document(kind="arena_pass", **changes):
    event = dict(id="first", kind=kind, name="Test season", source="Offline fixture",
                 oversea_start="2030-01-01T11:00:00+08:00",
                 oversea_end="2030-02-01T11:00:00+08:00",
                 cn_start="2030-01-22T11:00:00+08:00",
                 cn_end="2030-02-22T11:00:00+08:00", values={"max_level": 40})
    event.update(changes)
    return dict(schema_version=1, defaults={
        "arena_pass": {"max_level": 38, "source": "Legacy"},
        "shadow_commission": {"max_level": 30, "source": "Legacy"}},
        cn_delay_days={"free_gacha_20": 21}, events=[event])

def parse(data):
    return catalog.parse_info(json.dumps(data))

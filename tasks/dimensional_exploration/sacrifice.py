"""Pay a reviewed event cost using only positively identified normal loot."""

import hashlib

from module.base.button import ClickButton
from tasks.dimensional_exploration.assets.assets_dimensional_exploration import (
    SACRIFICE_CONFIRM, SACRIFICE_NORMAL_CORNER, SACRIFICE_REMOVE, SACRIFICE_SELECTED_MARK,
)
from tasks.dimensional_exploration.event import event_branch
from tasks.dimensional_exploration.vision import match_in


class SacrificeMixin:
    def reset_sacrifice(self):
        self._sacrifice_selected = None
        self._sacrifice_before_view = None
        self._sacrifice_stalls = 0
        self._sacrifice_swipes = 0

    def observe_sacrifice(self, state):
        # Network overlays never reach observations. Unknown animation frames
        # preserve selection proof; a positive non-picker page invalidates it.
        if state not in ("sacrifice", "unknown"):
            self.reset_sacrifice()

    def handle_sacrifice(self, vision):
        """Select a normal item, confirm its checkmark, then wait for results.

        Pages:
            in: reviewed event's one-item sacrifice picker
            out: event narration after payment
        """
        branch = event_branch(self.event_memory.pending)
        if branch is None or not branch.enabled or branch.cost.loot != 1 or self.event_memory.advanced:
            self.require_human("献祭页面缺少未完成的单件战利品事件记录，尚未消耗战利品。")
        counter, count = vision.sacrifice_counter(), vision.sacrifice_loot_count()
        if counter not in ((0, 1), (1, 1)) or count is None or count < 1:
            return False
        if counter == (1, 1):
            # A gold selected border hides the original item type. After a
            # restart (or changed selection/count), clear that reversible UI
            # selection and identify its ordinary border again. A checkmark
            # alone must never authorize sacrificing a special item.
            proof = self._sacrifice_selected
            if (proof is None or proof[1] != count
                    or match_in(vision.image, SACRIFICE_SELECTED_MARK, proof[0]) is None):
                self._sacrifice_selected = None
                if self.appear(SACRIFICE_REMOVE):
                    return self.click_action(SACRIFICE_REMOVE)
                return False
            if self.appear(SACRIFICE_CONFIRM) and vision.bright_text(SACRIFICE_CONFIRM):
                return self.click_action(SACRIFICE_CONFIRM)
            return False
        ordinary, special = vision.sacrifice_items()
        if ordinary:
            # Locate anew on every unselected frame. Do not retain coordinates
            # through scrolling, and never promote a pink special border to
            # an ordinary one just because its corner shape is identical.
            area = ordinary[0]
            # A delayed unselected frame can arrive after our click while the
            # action interval is still pending. Keep the ordinary-item proof
            # only while this frame independently confirms the same item and
            # count; otherwise the later checkmark would lose authorization
            # merely because the selection animation was slow.
            if self._sacrifice_selected != (area, count):
                self._sacrifice_selected = None
            if self.click_action(ClickButton(area, name="SacrificeNormalSelect")):
                self._sacrifice_selected = (area, count)
                return True
            return False
        self._sacrifice_selected = None
        if len(special) >= count:
            self.require_human("战利品都是特殊战利品，按策略保留，无法支付本次献祭。")
        x1, y1, x2, y2 = SACRIFICE_NORMAL_CORNER.search
        signature = hashlib.sha256(vision.image[y1:y2, x1:x2].tobytes()).digest()
        if self._sacrifice_before_view is not None:
            self._sacrifice_stalls = self._sacrifice_stalls + 1 if signature == self._sacrifice_before_view else 0
            self._sacrifice_before_view = None
        if self._sacrifice_stalls >= 2 or self._sacrifice_swipes >= 30:
            self.require_human("未找到可确认的普通战利品，保留特殊战利品并停止献祭。")
        if not self.action_ready():
            return False
        x, height = (x1 + x2) // 2, y2 - y1
        self.device.swipe((x, int(y1 + height * 0.8)), (x, int(y1 + height * 0.3)),
                          duration=(0.3, 0.4), name="ExplorationSacrificeList")
        self.interval_reset("ExplorationAction", interval=2)
        self._sacrifice_before_view = signature
        self._sacrifice_swipes += 1
        return True

# ruff: noqa: E402
from module.config import server as _test_server

_test_server.set_lang("global_cn")

"""Offline regressions for the September 18 runtime logs; never connects to a device."""
import ast
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

WORKTREE = Path(__file__).resolve().parents[2]
PROJECT_ROOT = WORKTREE
import module.config.server as server
server.set_lang("global_cn")
from tasks.base.ui import UI
from tasks.base.page import Page
from tasks.dungeon.prepare import CombatPrepare
from tasks.store.current import CurrentStore
from tasks.store.purchase import ItemPurchasePlan, PurchaseResult
from tasks.store.assets.assets_store_items import DAILY_FREE_ITEM
from tasks.activity.navigation import ActivityNavigationMixin


def timers(clock):
    class Timer:
        def __init__(self, limit, count=0):
            self.limit, self.deadline = limit, None
        def start(self):
            if self.deadline is None:
                self.reset()
            return self
        def reset(self):
            self.deadline = clock() + self.limit
            return self
        def clear(self):
            self.deadline = None
            return self
        def started(self):
            return self.deadline is not None
        def reached(self):
            return self.started() and clock() >= self.deadline
    return Timer


class Frames:
    def __init__(self, frames):
        self.frames, self.index = frames, -1
        self.device = SimpleNamespace(screenshot=self.screenshot, image=None)
        self.screenshot()
    def screenshot(self):
        self.index += 1
        if self.index >= len(self.frames):
            raise AssertionError("Flow did not finish on supplied screenshots")
        self.device.image = self.frames[self.index]
    @property
    def frame(self):
        return self.frames[self.index]


class FakePage:
    def __init__(self, name, parent=None):
        self.name, self.parent, self.check_button = name, parent, name
        self.links_need_match = set()
    def __str__(self):
        return self.name


class Navigation(Frames, UI):
    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.clicks = []
        self.recoveries = 0
        self.device.click = lambda button: self.clicks.append((self.frame['at'], button))
    def _ui_build_dynamic_links(self):
        return {}
    def interval_clear(self, *args):
        pass
    def ui_page_appear(self, page, interval=0):
        assert interval == 0, "Recognition must not be hidden by click cooldown"
        return page in self.frame['pages']
    def ui_page_confirm(self, page):
        return False
    def _ui_get_link_button(self, page, target):
        return f"{page}->{target}"
    def _ui_record_transition(self, *args):
        pass
    def ui_button_interval_reset(self, *args):
        pass
    def handle_ui_recovery(self):
        active = self.frame.get('network', False)
        self.recoveries += int(active)
        return active
    def ui_additional(self):
        return False
    def handle_popup_confirm(self):
        return False


class UiTests(unittest.TestCase):
    def run_route(self, frames, pages, destination):
        nav = Navigation(frames)
        with patch.object(Page, 'init_connection'), patch.object(Page, 'clear_connection'), patch.object(
            Page, 'iter_check_buttons', return_value=()
        ), patch.object(Page, 'iter_pages', side_effect=lambda: iter(pages)), patch(
            'tasks.base.ui.Timer', timers(lambda: nav.frame['at'])
        ):
            nav.ui_goto(destination)
        return nav
    def test_destination_arrives_without_waiting_for_retry(self):
        dest = FakePage('dest')
        source = FakePage('source', dest)
        nav = self.run_route([{'at':0,'pages':[source]}, {'at':0.3,'pages':[source]}, {'at':0.5,'pages':[dest]}], [source,dest], dest)
        self.assertEqual(nav.clicks, [(0,'source->dest')])
    def test_failed_back_retries_at_two_seconds(self):
        dest = FakePage('dest')
        source = FakePage('source', dest)
        frames = [{'at':t,'pages':[source]} for t in (0,0.3,1.9,2.1)] + [{'at':2.4,'pages':[dest]}]
        nav = self.run_route(frames, [source,dest], dest)
        self.assertEqual(nav.clicks, [(0,'source->dest'), (2.1,'source->dest')])
    def test_expected_page_precedes_overlapping_inventory_marker(self):
        dest = FakePage('dest')
        middle = FakePage('middle', dest)
        source = FakePage('source', middle)
        wrong = FakePage('inventory', dest)
        nav = self.run_route([{'at':0,'pages':[source]}, {'at':0.1,'pages':[wrong,source]}, {'at':0.3,'pages':[middle,wrong]}, {'at':0.6,'pages':[dest]}], [wrong,source,middle,dest], dest)
        self.assertEqual(nav.clicks, [(0,'source->middle'), (0.3,'middle->dest')])
    def test_network_dialog_precedes_destination_exit(self):
        dest = FakePage('dest')
        nav = self.run_route([{'at':0,'pages':[dest],'network':True}, {'at':0.5,'pages':[dest]}], [dest], dest)
        self.assertEqual(nav.index, 1)
        self.assertEqual(nav.recoveries, 1)
        self.assertEqual(nav.clicks, [])


class Prepare(Frames, CombatPrepare):
    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.actions = []
    def _is_prepare_page(self):
        return self.frame in ('off','ready')
    def _is_fast_combat_on(self):
        return self.frame == 'ready'
    def _handle_dungeon_additional(self):
        if self.frame == 'confirm':
            self.actions.append('confirm')
            return True
        return False
    def _is_fast_combat_locked(self):
        return False
    def _ensure_fast_combat_state(self, enabled):
        if self.frame == 'off':
            self.actions.append('enable')
            return False
        return True
    def _ocr_fast_combat_remaining_times(self):
        assert self.frame == 'ready'
        return 10
    def _combat_stage_stamina_cost(self):
        return 20
    def _combat_fast_count(self):
        return 10
    def _set_prepare_count(self, target, *args, **kwargs):
        self.actions.append(('count',target))
        return True


class PrepareTests(unittest.TestCase):
    def test_loading_and_confirmation_do_not_abort_preparation(self):
        prepare = Prepare(['off','loading','confirm','loading','ready'])
        with patch('tasks.dungeon.prepare.Timer', timers(lambda: prepare.index)):
            self.assertEqual(prepare._prepare_fast_combat(100), ('ready',5))
        self.assertEqual(prepare.actions, ['enable','confirm',('count',5)])
    def test_persistent_unknown_page_times_out(self):
        prepare = Prepare(['loading'] * 20)
        with patch('tasks.dungeon.prepare.Timer', timers(lambda: prepare.index)):
            self.assertEqual(prepare._prepare_fast_combat(100), ('failed',0))
        self.assertEqual(prepare.actions, [])
    def test_failed_toggle_does_not_reset_timeout_forever(self):
        prepare = Prepare(['off'] * 20)
        with patch('tasks.dungeon.prepare.Timer', timers(lambda: prepare.index)):
            self.assertEqual(prepare._prepare_fast_combat(100), ('failed',0))


class Store(Frames, CurrentStore):
    def __init__(self, frames):
        Frames.__init__(self, frames)
        self.index = -1  # purchase loop always requests a fresh screenshot
        self.closes, self.network_calls, self.confirms = [], [], []
        self._log_purchase_debug = Mock()
    def appear(self, button, **kwargs):
        if button.name == 'TOUCH_TO_CLOSE':
            return self.frame in ('reward','reward_wait','network','network_wait')
        if button.name == 'NETWORK_ERROR_ABNORMAL':
            return self.frame in ('network','network_wait')
        return False
    def handle_network_error(self, **kwargs):
        self.network_calls.append(self.index)
        return self.frame == 'network'
    def handle_touch_to_close(self, **kwargs):
        if self.frame == 'reward':
            self.closes.append(self.index)
            return True
        return False
    def ui_additional(self):
        return False
    def _detect_purchase_popup_layout(self):
        return 'single' if self.frame == 'confirm' else 'unknown'
    def _is_on_any_store_page(self):
        return self.frame in ('item','store','reward','reward_wait','network','network_wait')
    def _has_item(self, *args):
        return self.frame != "loading"
    def _click_purchase_target(self, item):
        return self.frame == 'item'
    def _click_purchase_confirm(self, layout, **kwargs):
        if self.frame == 'confirm' and layout != 'unknown':
            self.confirms.append(self.index)
            return True
        return False


class StoreTests(unittest.TestCase):
    def purchase(self, frames, popup=True):
        store = Store(frames)
        item = ItemPurchasePlan('daily_free_item', DAILY_FREE_ITEM, direct_click=True, requires_reward_popup=popup)
        with patch('tasks.store.current.Timer', timers(lambda: store.index)):
            result = store._purchase_item(item)
        return store, result
    def test_late_reward_popup_blocks_clean_store_frames(self):
        store, result = self.purchase(['item','confirm','store','store','store','reward','reward_wait','store'])
        self.assertTrue(result.success)
        self.assertEqual(store.index, 7)
        self.assertEqual(store.closes, [5])
    def test_loading_after_popup_close_is_not_completion(self):
        store, result = self.purchase(['item','confirm','reward','loading','loading','store'])
        self.assertTrue(result.success)
        self.assertEqual(store.index, 5)
    def test_repeated_network_errors_do_not_extend_reward_wait_forever(self):
        store, result = self.purchase(['item','confirm'] + ['network'] * 25)
        self.assertFalse(result.success)
        self.assertLessEqual(store.index, 21)
        self.assertEqual(store.closes, [])
    def test_network_close_is_not_counted_as_reward_close(self):
        store, result = self.purchase(['item','confirm','network','network_wait','store','reward','store'])
        self.assertTrue(result.success)
        self.assertEqual(store.network_calls, [2,3])
        self.assertEqual(store.closes, [5])
    def test_missing_reward_popup_fails_without_success(self):
        store, result = self.purchase(['item','confirm'] + ['store'] * 24)
        self.assertFalse(result.success)
        self.assertEqual(store.closes, [])
    def test_normal_purchase_does_not_require_reward_popup(self):
        store, result = self.purchase(['item','confirm','store'], popup=False)
        self.assertTrue(result.success)
        self.assertEqual(store.closes, [])
    def test_failed_or_unsettled_purchase_stops_next_item(self):
        for result, settled in ((PurchaseResult(False),True), (PurchaseResult(True),False)):
            store = object.__new__(CurrentStore)
            store.device = SimpleNamespace(screenshot=lambda: None)
            store._wait_purchase_cooldown_before_switch = Mock()
            store._item_ready_for_purchase = Mock(return_value=True)
            store._purchase_item = Mock(return_value=result)
            store._record_purchase_result = Mock()
            store._record_purchase_time = Mock()
            store._wait_store_ready_after_purchase = Mock(return_value=settled)
            item = ItemPurchasePlan('item',DAILY_FREE_ITEM)
            self.assertFalse(store._run_store_page_items('test',Mock(),[item,item]))
            self.assertEqual(store._purchase_item.call_count,1)
    def test_store_failure_schedules_retry_without_next_store(self):
        store = object.__new__(CurrentStore)
        store.device = SimpleNamespace(app_is_running=lambda: True)
        store.config = SimpleNamespace(task_delay=Mock())
        store._load_sub_store_entry_search = Mock()
        store.ui_goto = Mock()
        store._load_shared_item_search = Mock()
        store._build_free_store_items = Mock(return_value=[])
        store._run_store_page_items = Mock(return_value=False)
        store._run_inheritance_store = Mock()
        self.assertFalse(store.run())
        store._run_inheritance_store.assert_not_called()
        store.config.task_delay.assert_called_once_with(success=False)


class SidebarTests(unittest.TestCase):
    def test_boundary_detection_tolerates_partial_ocr(self):
        before = (('A',(0,100,100,120)), ('B',(0,200,100,220)), ('C',(0,300,100,320)))
        self.assertTrue(ActivityNavigationMixin._activity_scroll_unchanged(before,before[:2]))
        moved = (('A',(0,50,100,70)), ('B',(0,150,100,170)))
        self.assertFalse(ActivityNavigationMixin._activity_scroll_unchanged(before,moved))
        self.assertFalse(ActivityNavigationMixin._activity_scroll_unchanged(before,()))
    def test_ocr_jitter_does_not_block_scrolling(self):
        from tests.support.history_cn_september_update import NavigationTests
        server.set_lang('cn')
        nav, success = NavigationTests().navigate([
            {'text':'OtherA','y':100}, {'text':'OtherB','y':130},
            {}, {}, {'selected':True}
        ])
        self.assertTrue(success)
        self.assertEqual(len(nav.swipes),1)
        self.assertGreater(nav.swipes[0][0][1],nav.swipes[0][1][1])


class LogTests(unittest.TestCase):
    def test_application_log_literals_are_english(self):
        violations = []
        for name in subprocess.check_output(['git','ls-files','*.py'],text=True).splitlines():
            if not name.startswith(('tasks/','module/')):
                continue
            tree = ast.parse(Path(name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id == 'logger'):
                    continue
                if any(isinstance(n,ast.Constant) and isinstance(n.value,str) and any('\u4e00' <= c <= '\u9fff' for c in n.value) for arg in node.args for n in ast.walk(arg)):
                    violations.append((name,node.lineno))
        self.assertEqual(violations,[])

"""Penguin pixels, bounded purchase sessions and missed-click regression."""
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from module.config import server
from module.exception import GameStuckError, ScriptError

server.set_lang('global_cn')

from tasks.sanctuary.buy_penguins import BuyPenguins  # noqa: E402
from tasks.sanctuary.assets.assets_sanctuary_buy_penguins import (  # noqa: E402
    PENGUIN_BUY, PENGUIN_PURCHASE_CHECK, PENGUIN_REWARD_CHECK,
)
from tests.support.buy_penguins import CAPTURES, Frame, PenguinReplay, pixel_task  # noqa: E402
from tests.support.offline import ControlledClock, ROOT, retained_directory  # noqa: E402


class PenguinCaptureTests(unittest.TestCase):
    def setUp(self):
        server.set_lang('global_cn')

    def test_modal_precedence_and_clear_forest(self):
        for scene in CAPTURES:
            task = pixel_task(scene)
            with self.subTest(scene=scene):
                self.assertEqual(task._forest_is_ready(), scene == 'forest')
                self.assertEqual(task._shop_is_ready(), scene == 'shop')
                self.assertEqual(task.appear(PENGUIN_PURCHASE_CHECK), scene in ('one', 'max'))
                self.assertEqual(task.appear(PENGUIN_REWARD_CHECK), scene == 'reward')

    def test_captured_balance_unit_price_and_quantities(self):
        self.assertEqual(pixel_task('shop')._read_shop_values(), (1213099, 102))
        self.assertEqual(pixel_task('one')._read_purchase_values(), ((1, 50), 102))
        self.assertEqual(pixel_task('max')._read_purchase_values(), ((50, 50), 5100))

    def test_chinese_assets_dispatch_on_both_servers(self):
        # These reviewed captures have an unconfirmed source server. This
        # checks shared-asset dispatch, never claims two-server live coverage.
        for language in ('cn', 'global_cn'):
            server.set_lang(language)
            self.assertTrue(pixel_task('shop')._shop_is_ready())
            self.assertTrue(pixel_task('forest')._forest_is_ready())

    def test_dim_purchase_control_does_not_hide_the_shop_balance(self):
        task = pixel_task('shop')
        task.device.image = task.device.image.copy()
        left, top, right, bottom = PENGUIN_BUY.area
        # Synthetic disabled control, built on a reviewed shop frame. There
        # is no real low-stigma screenshot in this corpus. Page identity must
        # use the bright shop egg, allowing the resource check to stop safely.
        task.device.image[top:bottom, left:right] = 20
        self.assertFalse(PENGUIN_BUY.match_color(task.device.image))
        self.assertTrue(task._shop_is_ready())
        self.assertEqual(task._read_shop_values(), (1213099, 102))


class PenguinRuleTests(unittest.TestCase):
    def test_gui_batch_count_save_feedback_and_reload(self):
        from pathlib import Path

        from module.config.config_updater import ConfigUpdater
        from module.config.deep import deep_get

        # Import the real save handler without reading deployment settings or
        # starting browser infrastructure. No player profile is used here.
        with (patch('module.webui.config.DeployConfig.read'),
              patch('module.webui.patch.patch_executor'),
              patch('module.webui.patch.patch_mimetype')):
            from module.webui.app import AlasGUI

        key = 'BuyPenguins.BuyPenguins.BatchCount'
        updater = ConfigUpdater()
        translations = json.loads((ROOT / 'module/config/i18n/zh-CN.json').read_text(encoding='utf-8'))
        gui = SimpleNamespace(
            ALAS_ARGS=updater.args,
            alas_config_hidden=set(),
            pin_remove_invalid_mark=Mock(), pin_set_invalid_mark=Mock(),
            pin_set_hidden_arg=Mock(), pin_remove_hidden_arg=Mock(),
        )
        with retained_directory('penguin-gui-save') as directory:
            profile = str(Path(directory) / 'synthetic.json')
            pins = {}
            with (patch('module.config.config_updater.filepath_config', return_value=profile),
                  patch('module.webui.app.filepath_config', return_value=profile),
                  patch('module.webui.app.pin', pins),
                  patch('module.webui.app.t', side_effect=lambda key: deep_get(translations, key)),
                  patch('module.webui.app.toast') as toast,
                  patch('module.webui.app.logger.exception') as error):
                # Reload through the production updater, as changing pages and
                # launching the tool do. A displayed input alone is not saved.
                for raw, expected in [('3', 3), ('12', 12), ('', 1)]:
                    toast.reset_mock()
                    AlasGUI._save_config(gui, {key: raw}, 'synthetic', updater)
                    error.assert_not_called()
                    toast.assert_called_once_with(
                        deep_get(translations, 'Gui.Toast.ConfigSaved'),
                        duration=1, position='right', color='success',
                    )
                    reloaded = updater.read_file('synthetic')
                    self.assertEqual(deep_get(reloaded, key), expected)
                    self.assertIs(type(deep_get(reloaded, key)), int)
                    self.assertFalse(reloaded['BuyPenguins']['Scheduler']['Enable'])
                self.assertEqual(pins[key.replace('.', '_')], 1)

                before = Path(profile).read_bytes()
                for raw in ('0', '-1', '1.5', 'abc', '1e2', '+2', '2.0'):
                    with self.subTest(invalid=raw):
                        toast.reset_mock()
                        AlasGUI._save_config(gui, {key: raw}, 'synthetic', updater)
                        error.assert_not_called()
                        toast.assert_not_called()
                        gui.pin_set_invalid_mark.assert_called_with([key])
                        self.assertEqual(Path(profile).read_bytes(), before)

    def test_strict_numeric_and_quantity_validation(self):
        for text, expected in [('1,213,099', 1213099), ('102', 102), ('0', 0),
                               ('', None), ('1,21', None), ('5,100红叶', None), ('-1', None)]:
            self.assertEqual(BuyPenguins.parse_number(text), expected)
        for text, expected in [('50/50', (50, 50)), ('1/50', (1, 50)),
                               ('0/50', None), ('51/50', None), ('50/0', None), ('50', None)]:
            self.assertEqual(BuyPenguins.parse_quantity(text), expected)

    def test_tool_config_defaults_dispatch_and_scheduler_disabled(self):
        def source(name):
            return yaml.safe_load((ROOT / f'module/config/argument/{name}.yaml').read_text(encoding='utf-8'))
        tasks = source('task')
        self.assertEqual(tasks['Tool']['tasks']['BuyPenguins'], ['Scheduler', 'BuyPenguins'])
        self.assertEqual(source('argument')['BuyPenguins']['BatchCount']['value'], 1)
        self.assertFalse(source('override')['BuyPenguins']['Scheduler']['Enable']['value'])
        from module.config.config_updater import ConfigGenerator
        merged = ConfigGenerator().args['BuyPenguins']
        for option in merged['Scheduler'].values():
            self.assertEqual(option['display'], 'hide')
        self.assertEqual(merged['Scheduler']['Enable']['option'], [False])
        self.assertEqual(merged['BuyPenguins']['BatchCount']['value'], 1)
        from module.webui.submodule.utils import get_tool_runner
        self.assertTrue(callable(get_tool_runner('BuyPenguins')))
        # Check the shipped menu/config artifacts without loading a player's
        # profile. A stale generator result would hide this manual tool or
        # fail to bind its batch count even though the YAML sources are valid.
        generated = json.loads((ROOT / 'module/config/argument/args.json').read_text(encoding='utf-8'))
        menu = json.loads((ROOT / 'module/config/argument/menu.json').read_text(encoding='utf-8'))
        self.assertEqual(generated['BuyPenguins'], json.loads(json.dumps(merged, default=str)))
        self.assertIn('BuyPenguins', menu['Tool']['tasks'])
        self.assertEqual(menu['Tool']['page'], 'tool')
        from module.config.config_generated import GeneratedConfig
        self.assertEqual(GeneratedConfig.BuyPenguins_BatchCount, 1)
        from module.config.config_updater import ConfigUpdater
        updated = ConfigUpdater().config_update({'BuyPenguins': {'Scheduler': {'Enable': True}}})
        self.assertFalse(updated['BuyPenguins']['Scheduler']['Enable'])
        self.assertEqual(updated['BuyPenguins']['BuyPenguins']['BatchCount'], 1)
        for language in ('zh-CN', 'zh-TW', 'en-US', 'ja-JP', 'es-ES'):
            translated = json.loads((ROOT / f'module/config/i18n/{language}.json').read_text(encoding='utf-8'))
            for item in (translated['Task']['BuyPenguins'], translated['BuyPenguins']['_info'],
                         translated['BuyPenguins']['BatchCount']):
                for value in item.values():
                    self.assertTrue(value)
                    self.assertNotIn('BuyPenguins.', value)

    def test_import_and_forest_route_in_isolated_servers(self):
        code = '''
import json, sys
from module.config import server
server.server, lang = sys.argv[1:]
server.set_lang(lang)
from tasks.sanctuary.buy_penguins import BuyPenguins
from tasks.base.page import Page, page_sanctuary, page_sanctuary_forest
assert page_sanctuary_forest in page_sanctuary.links
assert page_sanctuary in page_sanctuary_forest.links
assert len(Page.all_pages) > 0
print(json.dumps({'language': lang, 'import': True}))
'''
        for name, language in [('CN-Official', 'cn'), ('OVERSEA-Play', 'global_cn'),
                               ('OVERSEA-Play', 'global_en')]:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', code, name, language],
                                    cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unsupported_language_and_invalid_batches_do_not_access_device(self):
        task = BuyPenguins.__new__(BuyPenguins)
        task.config = SimpleNamespace(Emulator_GameLanguage='en')
        self.assertEqual(task.run(), 0)
        for value in (0, -1, '1', True):
            with self.assertRaises(ScriptError):
                task.buy_batches(value)

    def test_manual_run_reuses_known_overlay_and_navigates_other_pages(self):
        for known_overlay in (True, False):
            task = BuyPenguins.__new__(BuyPenguins)
            task.config = SimpleNamespace(Emulator_GameLanguage='auto', BuyPenguins_BatchCount=1)
            task.device = Mock()
            task.device.app_is_running.return_value = True
            task._forest_is_ready = lambda: False
            task._shop_is_ready = lambda: False
            task.appear = lambda *args: known_overlay
            task.ui_goto = Mock()
            task.buy_batches = Mock(return_value=1)
            self.assertEqual(task.run(), 1)
            self.assertEqual(task._ocr_lang(), 'cn')
            task.device.screenshot.assert_called_once()
            self.assertEqual(task.ui_goto.call_count, int(not known_overlay))
            task.buy_batches.assert_called_once_with(1)


class PenguinReplayTests(unittest.TestCase):
    def replay(self, frames, batches=1):
        with ControlledClock() as clock:
            task = PenguinReplay(frames, clock)
            result = task.buy_batches(batches)
        return task, result, [name for _, name in task.device.actions]

    def test_full_batch_closes_overlays_and_counts_exact_debit_once(self):
        task, result, actions = self.replay([
            Frame('forest'), Frame(), Frame(), Frame('purchase', quantity=(1, 50), price=102),
            Frame('purchase'), Frame('reward'), Frame('reward'),
            Frame(balance=1207999), Frame(balance=1207999), Frame('forest'),
        ])
        self.assertEqual(result, 1)
        self.assertEqual(task.completed_batches, 1)
        self.assertEqual(actions, ['ALTAR_OF_GROWTH', 'PENGUIN_BUY', 'PENGUIN_MAX',
                                   'PENGUIN_CONFIRM', 'PENGUIN_REWARD_CLOSE', 'PENGUIN_SHOP_CLOSE'])

    def test_entry_max_confirm_and_close_retry_persistent_frames(self):
        task, result, actions = self.replay(
            [Frame('forest')] * 4 + [Frame()] * 5
            + [Frame('purchase', quantity=(1, 50), price=102)] * 4
            + [Frame('purchase')] * 4 + [Frame('reward')] * 4
            + [Frame(balance=1207999)] * 5 + [Frame('forest')])
        self.assertEqual(result, 1)
        for name in ('ALTAR_OF_GROWTH', 'PENGUIN_BUY', 'PENGUIN_MAX',
                     'PENGUIN_CONFIRM', 'PENGUIN_REWARD_CLOSE', 'PENGUIN_SHOP_CLOSE'):
            self.assertEqual(actions.count(name), 2, name)
        confirm_frame = next(i for i, name in task.device.actions if name == 'PENGUIN_CONFIRM')
        self.assertGreaterEqual(confirm_frame, 11)

    def test_old_shop_and_delayed_debit_never_open_another_purchase(self):
        task, result, actions = self.replay([
            Frame(), Frame(), Frame('purchase'), Frame('reward'),
            Frame(), Frame(), Frame(), Frame(balance=1207999),
            Frame(balance=None), Frame(balance=1207999), Frame(balance=1207999), Frame('forest'),
        ])
        self.assertEqual(result, 1)
        self.assertEqual(actions.count('PENGUIN_BUY'), 1)
        self.assertEqual(task.device.actions[-1], (10, 'PENGUIN_SHOP_CLOSE'))

    def test_missing_reward_still_settles_from_two_exact_debit_frames(self):
        _, result, actions = self.replay([
            Frame(), Frame(), Frame('purchase'), Frame('unknown'),
            Frame(balance=1207999), Frame(balance=1207999), Frame('forest'),
        ])
        self.assertEqual(result, 1)
        self.assertNotIn('PENGUIN_REWARD_CLOSE', actions)

    def test_two_batches_reset_quantity_and_payment_baseline(self):
        _, result, actions = self.replay([
            Frame(), Frame(), Frame('purchase'), Frame('reward'),
            Frame(balance=1207999), Frame(balance=1207999), Frame(balance=1207999),
            Frame('purchase', quantity=(1, 50), price=102), Frame('purchase'), Frame('reward'),
            Frame(balance=1202899), Frame(balance=1202899), Frame('forest'),
        ], batches=2)
        self.assertEqual(result, 2)
        self.assertEqual(actions.count('PENGUIN_BUY'), 2)
        self.assertEqual(actions.count('PENGUIN_CONFIRM'), 2)
        self.assertEqual(actions.count('PENGUIN_MAX'), 1)

    def test_insufficient_balance_stops_without_partial_batch(self):
        task, result, actions = self.replay([
            Frame(balance=5099), Frame(balance=5099), Frame(balance=5099), Frame('forest'),
        ])
        self.assertEqual(result, 0)
        self.assertEqual(task.stop_reason, 'insufficient_stigma')
        self.assertEqual(actions, ['PENGUIN_SHOP_CLOSE'])

    def test_manual_dialog_cancels_before_any_measured_purchase(self):
        _, result, actions = self.replay([
            Frame('purchase'), Frame('purchase'), Frame(), Frame(), Frame('purchase'),
            Frame(balance=1207999), Frame(balance=1207999), Frame('forest'),
        ])
        self.assertEqual(result, 1)
        self.assertEqual(actions[0], 'PENGUIN_CANCEL')
        self.assertEqual(actions.count('PENGUIN_CONFIRM'), 1)

    def test_lower_maximum_cancels_and_stops(self):
        _, result, actions = self.replay([
            Frame(), Frame(), Frame('purchase', quantity=(10, 10), price=1020),
            Frame(balance=1020), Frame('forest'),
        ])
        self.assertEqual(result, 0)
        self.assertEqual(actions, ['PENGUIN_BUY', 'PENGUIN_CANCEL', 'PENGUIN_SHOP_CLOSE'])

    def test_changed_quote_cancels_and_reads_new_price(self):
        _, result, actions = self.replay([
            Frame(), Frame(), Frame('purchase', price=6000),
            Frame(unit=120), Frame(unit=120), Frame('purchase', price=6000),
            Frame(balance=1207099, unit=120), Frame(balance=1207099, unit=120), Frame('forest'),
        ])
        self.assertEqual(result, 1)
        self.assertEqual(actions.count('PENGUIN_CANCEL'), 1)
        self.assertEqual(actions.count('PENGUIN_CONFIRM'), 1)

    def test_network_overlay_breaks_consecutive_debit_evidence(self):
        task, result, actions = self.replay([
            Frame(), Frame(), Frame('purchase'), Frame(balance=1207999), Frame('network'),
            Frame(balance=1207999), Frame(balance=1207999), Frame('forest'),
        ])
        self.assertEqual(result, 1)
        self.assertIn('network_retry', actions)
        self.assertEqual(task.device.actions[-1], (6, 'PENGUIN_SHOP_CLOSE'))

    def test_bad_ocr_wrong_debit_and_permanent_dialog_fail_without_counting(self):
        scenarios = [
            [Frame(balance=None)] * 35,
            [Frame(), Frame(), Frame('purchase')] + [Frame(balance=1208000)] * 35,
            [Frame(), Frame()] + [Frame('purchase', quantity=None, price=None)] * 35,
            [Frame(), Frame(), Frame('purchase')] + [Frame('purchase')] * 35,
        ]
        for frames in scenarios:
            with self.subTest(frames=frames[:3]), ControlledClock() as clock:
                task = PenguinReplay(frames, clock)
                with self.assertRaises(GameStuckError):
                    task.buy_batches(1)
                self.assertEqual(task.completed_batches, 0)
                self.assertLessEqual(sum(name == 'PENGUIN_BUY' for _, name in task.device.actions), 1)

    def test_timeout_is_not_extended_by_failed_click_retries(self):
        with ControlledClock() as clock:
            task = PenguinReplay([Frame()] * 40, clock)
            with self.assertRaises(GameStuckError):
                task.buy_batches(1)
            self.assertGreater(sum(name == 'PENGUIN_BUY' for _, name in task.device.actions), 1)
            self.assertLess(task.device.index, 39)

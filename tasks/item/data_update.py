import re

from module.base.timer import Timer
from module.logger import logger
from module.ocr.ocr import Digit, DigitCounter
from tasks.base.page import page_item
from tasks.item.assets.assets_item_data import OCR_DATA, OCR_RELIC
from tasks.item.keywords import KEYWORDS_ITEM_TAB
from tasks.item.ui import ItemUI
from tasks.planner.model import PlannerMixin


class DataDigit(Digit):
    def after_process(self, result):
        result = re.sub(r'[l|]', '1', result)
        result = re.sub(r'[oO]', '0', result)
        return super().after_process(result)


class RelicOcr(DigitCounter):
    def after_process(self, result):
        result = re.sub(r'[l1|]3000', '/3000', result)
        result = re.sub(r'[oO]', '0', result)
        return super().after_process(result)


class DataUpdate(ItemUI, PlannerMixin):
    def _get_data(self):
        """
        Page:
            in: page_item, KEYWORDS_ITEM_TAB.UpgradeMaterials
        """
        ocr = DataDigit(OCR_DATA)

        timeout = Timer(2, count=6).start()
        credit, jade = 0, 0
        for _ in self.loop():
            data = ocr.detect_and_ocr(self.device.image)
            if len(data) == 2:
                credit, jade = [int(re.sub(r'\s', '', d.ocr_text)) for d in data]
                if credit > 0 or jade > 0:
                    break

            logger.warning(f'Invalid credit and stellar jade: {data}')
            if timeout.reached():
                logger.warning('Get data timeout')
                break

        logger.attr('Gold', credit)
        logger.attr('Skystone', jade)
        return credit, jade

    def _get_equipment_inventory(self):
        """
        Page:
            in: page_item, KEYWORDS_ITEM_TAB.Relics
        """
        ocr = RelicOcr(OCR_RELIC)
        timeout = Timer(2, count=6).start()
        current = 0
        total = 0
        for _ in self.loop():
            current, _, total = ocr.ocr_single_line(self.device.image)
            if total > 0 and 0 <= current <= total:
                break
            logger.warning(f'Invalid equipment inventory: {current}/{total}')
            if timeout.reached():
                logger.warning('Get equipment inventory timeout')
                break

        logger.attr('EquipmentInventory', f'{current}/{total}')
        return current, total

    def run(self):
        self.ui_ensure(page_item, acquire_lang_checked=False)
        # item tab stays at the last used tab, switch to UpgradeMaterials
        self.item_goto(KEYWORDS_ITEM_TAB.UpgradeMaterials, wait_until_stable=False)
        gold, skystone = self._get_data()

        self.item_goto(KEYWORDS_ITEM_TAB.Relics, wait_until_stable=False)
        equipment, equipment_total = self._get_equipment_inventory()

        with self.config.multi_set():
            self.config.stored.Credit.value = gold
            self.config.stored.StallerJade.value = skystone
            self.config.stored.E7Gold.value = gold
            self.config.stored.E7Skystone.value = skystone
            if equipment_total > 0:
                self.config.stored.E7EquipmentInventory.set(equipment, equipment_total)
            if equipment_total == self.config.stored.Relic.FIXED_TOTAL:
                self.config.stored.Relic.value = equipment
            self.config.task_delay(server_update=True)
            # Sync to planner
            require = self.config.cross_get('Dungeon.Planner.Item_Credit.total', default=0)
            if require:
                self.config.cross_set('Dungeon.Planner.Item_Credit.value', gold)
                self.config.cross_set('Dungeon.Planner.Item_Credit.time', self.config.stored.Credit.time)
                self.planner_write()

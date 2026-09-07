from alasio.config.entry.const import ConfigConst as ConfigConst_, ModEntryInfo
from alasio.config_dev.gen_index import IndexGenerator
from alasio.ext import env
from alasio.ext.path import PathStr
from alasio.logger import logger

entry = ModEntryInfo(
    name='aes',
    path_config='module/config_alasio',
    asset_lang=dict.fromkeys(['cn', 'global_cn', 'global_en']),
    gui_language=dict.fromkeys(['zh-CN', 'en-US', 'ja-JP', 'zh-TW']),
)
entry.root = PathStr.new(__file__).uppath(3)


class ConfigConst(ConfigConst_):
    SCHEDULER_PRIORITY = """
    Restart
    > SpecialActivity > Mail > SanctuaryDaily > SanctuaryMonthly
    > Knights > Arena > Store > PetsGift
    > SecretShop > Combat > Gacha > MissionReward > Pets > DataUpdate > CommunityAio
    > CombatFarm > CommunityAuth
    """


if __name__ == '__main__':
    env.set_project_root(env.ALASIO_ROOT)
    logger.info(f'ModEntry: {entry}')
    self = IndexGenerator(entry)
    self.generate()

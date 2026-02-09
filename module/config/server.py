"""
This file stores server, such as 'cn', 'en'.
Use 'import module.config.server as server' to import, don't use 'from xxx import xxx'.
"""
lang = 'cn'  # Setting default to cn, will avoid errors when using dev_tools
server = 'CN-Official'

# 支持的语言/assets目录
# cn = 国服简中
# global_cn = 国际服简中
# global_en = 国际服英文
# 未来可扩展: global_jp, global_kr, global_tw 等
VALID_LANG = ['cn', 'global_cn', 'global_en']
VALID_SERVER = {
    'CN-Official': 'com.zlongame.cn.epicseven',
    'OVERSEA-Play': 'com.stove.epic7.google',
}
VALID_PACKAGE = set(list(VALID_SERVER.values()))
# Epic Seven doesn't have cloud gaming version
VALID_CLOUD_SERVER = {}
VALID_CLOUD_PACKAGE = set()

DICT_PACKAGE_TO_ACTIVITY = {
    'com.zlongame.cn.epicseven': 'kr.supercreative.epic7.AppActivity',
    'com.stove.epic7.google': 'kr.supercreative.epic7.AppActivity',
}


def set_lang(lang_: str):
    """
    Change language and this will affect globally,
    including assets and language specific methods.

    Args:
        lang_: package name or server.
    """
    global lang
    lang = lang_

    from module.base.resource import release_resources
    release_resources()


def to_server(package_or_server: str, before: str = '') -> str:
    """
    Convert package/server to server.
    """
    for key, value in VALID_SERVER.items():
        if value == package_or_server:
            return key
        if key == package_or_server:
            return key

    raise ValueError(f'Package invalid: {package_or_server}')


def to_package(package_or_server: str, is_cloud=False) -> str:
    """
    Convert package/server to package.
    """
    for key, value in VALID_SERVER.items():
        if value == package_or_server:
            return value
        if key == package_or_server:
            return value

    raise ValueError(f'Server invalid: {package_or_server}')

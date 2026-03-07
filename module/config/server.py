"""
This file stores server, such as 'cn', 'en'.
Use 'import module.config.server as server' to import, don't use 'from xxx import xxx'.
"""
lang = 'cn'  # Setting default to cn, will avoid errors when using dev_tools
server = 'CN-Official'

SERVER_FAMILY_CN = 'CN'
SERVER_FAMILY_OVERSEA = 'OVERSEA'
CANONICAL_SERVER = {
    SERVER_FAMILY_CN: 'CN-Official',
    SERVER_FAMILY_OVERSEA: 'OVERSEA-Play',
}

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


def map_assets_lang(server_name: str, game_lang: str) -> str:
    """
    Map game language + server to assets language.

    Args:
        server_name: Server key in VALID_SERVER, such as 'CN-Official', 'OVERSEA-Play'
        game_lang: Emulator_GameLanguage value, such as 'cn', 'en', 'auto'

    Returns:
        str: Assets language in VALID_LANG
    """
    if game_lang == 'auto' or not game_lang:
        game_lang = 'cn'

    if is_oversea_server(server_name):
        return 'global_en' if game_lang == 'en' else 'global_cn'

    return 'cn'


def normalize_server(package_or_server: str = '') -> str:
    """
    Normalize package/server input to a server-like string.

    Args:
        package_or_server: Package name or server key. Empty means current global server.

    Returns:
        str: Normalized server string if known, otherwise an empty string.
    """
    value = package_or_server or server
    if not value:
        return ''

    try:
        return to_server(value)
    except ValueError:
        return ''


def server_family(package_or_server: str = '') -> str:
    """
    Get server family, such as 'CN' or 'OVERSEA'.

    Args:
        package_or_server: Package name or server key. Empty means current global server.
    """
    value = normalize_server(package_or_server)
    family, _, _ = value.partition('-')
    return family


def canonical_server(package_or_server: str = '') -> str:
    """
    Collapse server aliases to one canonical server key per family.

    Args:
        package_or_server: Package name or server key. Empty means current global server.
    """
    value = normalize_server(package_or_server)
    if not value:
        return ''

    family, _, _ = value.partition('-')
    return CANONICAL_SERVER.get(family, value)


def is_cn_server(package_or_server: str = '') -> bool:
    """
    Args:
        package_or_server: Package name or server key. Empty means current global server.
    """
    return server_family(package_or_server) == SERVER_FAMILY_CN


def is_oversea_server(package_or_server: str = '') -> bool:
    """
    Args:
        package_or_server: Package name or server key. Empty means current global server.
    """
    return server_family(package_or_server) == SERVER_FAMILY_OVERSEA


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

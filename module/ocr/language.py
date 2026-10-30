"""Normalize recognition languages independently of the asset namespace."""


def resolve_ocr_lang(config) -> str:
    lang = getattr(config, "Emulator_GameLanguage", "cn")
    if lang in ("auto", "", None, "cn", "global_cn", "zh", "zh_cn"):
        return "cn"
    if lang in ("en", "global_en", "en_us"):
        return "en"
    if lang in ("jp", "ja", "ja_jp"):
        return "jp"
    if lang in ("tw", "zh_tw"):
        return "tw"
    return "cn"

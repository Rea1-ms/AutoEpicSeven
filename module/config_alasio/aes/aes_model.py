import typing as t

import alasio.config.alasio.group_export as a
import msgspec as m
import typing_extensions as e


# This file was auto-generated, do not modify it manually. To generate:
# ``` python -m module.config.gen ```

class Game(a.GroupBase):
    PackageName: t.Literal['auto', 'CN-Official', 'OVERSEA-Play'] = 'auto'
    GameLanguage: t.Literal['auto', 'cn', 'en'] = 'cn'
    GameClient: t.Literal['android', 'cloud_android'] = 'android'

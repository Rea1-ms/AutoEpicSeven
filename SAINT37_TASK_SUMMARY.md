# 圣女 3-7 每日副本功能任务总结

## 当前状态

- 当前修改只更新了源码、配置和图片素材，`aes.exe` 尚未重新打包更新。
- 新增功能位于每日副本 `Combat` 任务中，副本类型新增 `Saint37`，中文界面显示为“圣女3-7”。
- 功能目标是从大厅出发，自动进入国服支线故事里的“圣女追悼文”3-7“灰色田野”，然后复用现有宠物重复战斗逻辑刷取。

## 已完成内容

- 新增 `Saint37` 副本类型配置：
  - `module/config/argument/argument.yaml`
  - `module/config/argument/args.json`
  - `module/config/config_generated.py`
  - `module/config/config_updater.py`
- 新增多语言显示项：
  - `module/config/i18n/zh-CN.json`
  - `module/config/i18n/zh-TW.json`
  - `module/config/i18n/en-US.json`
  - `module/config/i18n/es-ES.json`
  - `module/config/i18n/ja-JP.json`
- 新增圣女 3-7 流程代码：
  - `tasks/combat/saint37.py`
- 接入原每日副本主流程：
  - `tasks/combat/combat.py`
  - `tasks/combat/plan.py`
  - `tasks/combat/runtime.py`
- 新增国服识图按钮定义：
  - `tasks/combat/assets/assets_combat_saint37.py`
- 已将原 `3-7素材` 文件夹内素材移动到：
  - `assets/cn/combat/saint37/`
- 已新增圣女 3-7 专用奖励装备出售流程素材：
  - `assets/cn/combat/saint37_cleanup/`

## 当前自动化流程

1. 从大厅识别并点击右侧 `支线故事`。
2. 在支线故事页点击右下角 `特别的时间之书`。
3. 在时间之书列表中寻找并点击 `圣女追悼文`。
4. 如果列表中没找到，会向下滚动最多 8 次继续查找。
5. 选中圣女追悼文后点击下方绿色 `Episode`。
6. 进入圣女追悼文详情页后点击右下角 `冒险`。
7. 在地图页点击 `3-7 灰色田野`。
8. 点击右下角 `准备战斗`。
9. 在辅助英雄页点击 `选择队伍`。
10. 进入准备战斗页后，复用现有宠物重复战斗次数设置与启动逻辑。
11. 圣女 3-7 重复战斗完成后，打开战斗完成窗口里的背包奖励。
12. 在“获得的道具”窗口点击整理，点击快速选择，再点击出售。
13. 确认出售本次奖励窗口中被快速选择选中的装备。
14. 关闭奖励窗口，回到原有完成页关闭逻辑。

## 素材命名

原素材已改为英文名：

- `1.png` -> `LOBBY_SIDE_STORY_ENTRY.png`
- `2.png` -> `SIDE_STORY_TIME_BOOK_ENTRY.png`
- `3.png` -> `TIME_BOOK_SAINT_MEMORIAL.png`
- `4.png` -> `SAINT_MEMORIAL_DETAIL.png`
- `5.png` -> `SAINT37_MAP.png`
- `6.png` -> `SAINT37_SUPPORTER.png`
- `顺序.txt` -> `FLOW.md`

## 验证情况

- 已通过 Python 语法检查：
  - `tasks/combat/assets/assets_combat_saint37.py`
  - `tasks/combat/saint37.py`
  - `tasks/combat/combat.py`
  - `tasks/combat/plan.py`
  - `tasks/combat/runtime.py`
- 已通过 JSON 格式检查：
  - `module/config/argument/args.json`
  - `module/config/i18n/zh-CN.json`
- `python -m module.config.config_updater` 当前环境缺少 `cached_property` 依赖，未能完整运行；相关生成文件已手动同步。

## 尚未完成

- 尚未重新构建 `aes.exe`。
- 尚未进行模拟器实机跑通验证。
- 尚未确认所有异常弹窗素材，例如首次剧情、活动未开放、未解锁、辅助英雄为空、体力不足等。

## 可能缺少的素材

如果实机测试卡住，优先补以下截图：

- 首次进入圣女追悼文时出现的剧情/提示/确认弹窗。
- 活动或特别时间之书未开放时的提示弹窗。
- 3-7 未解锁或未通关时的提示弹窗。
- 辅助英雄列表为空、无需辅助、或选择队伍按钮样式变化的页面。
- 体力不足、背包满、网络异常之外的特殊提示弹窗。
- 奖励页没有可出售装备时的快速选择/出售按钮状态。
- 出售后奖励窗口关闭按钮样式变化。

## 爆仓处理素材命名

- `1.png` -> `REPEAT_COMPLETE_MARK.png`
- `2.png` -> `REPEAT_RESULT_WINDOW.png`
- `3.png` -> `REWARD_ITEM_WINDOW.png`
- `4.png` -> `REWARD_ITEM_MANAGE.png`
- `5.png` -> `REWARD_ITEM_SELL_SELECTED.png`
- `6.png` -> `REWARD_ITEM_SELL_CONFIRM.png`
- `7.png` -> `REWARD_ITEM_AFTER_SELL.png`
- `顺序.txt` -> `FLOW.md`

## 后续建议

- 先用源码方式实机测试 `Combat.Domain = Saint37`。
- 确认流程跑通后再重新打包 `aes.exe`。
- 如果日志出现 `Combat Saint37: ... missing` 或 `timeout`，根据日志对应步骤补充截图素材。

## aes.exe 打包方式

原项目的 Windows 桌面端打包方式在 `webapp` 目录执行：

```bash
cd webapp
bun install
bun run compile
```

说明：

- `bun install` 只需要首次安装依赖或依赖变更后执行，后续可跳过。
- `bun run compile` 会先构建 webapp，再通过 Electron Builder 编译桌面端。
- 编译输出通常位于 `webapp/dist/`，具体 exe 位置以 Electron Builder 输出日志为准。

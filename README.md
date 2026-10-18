**| [English](README_en.md) | 简体中文 | [Español](README_es.md) | [日本語](README_ja.md) |**


# AutoEpicSeven

EpicSeven auto script | 第七史诗脚本，基于第二代 Alas 框架，由 [StarRailCopilot](https://github.com/LmeSzinc/StarRailCopilot) 修改而来

![gui](https://raw.githubusercontent.com/Rea1-ms/AutoEpicSeven/refs/heads/master/README.assets/gui_cn.png)

![setting](https://raw.githubusercontent.com/Rea1-ms/AutoEpicSeven/refs/heads/master/README.assets/setting_cn.png)

## 功能

- **打本**：讨伐，精灵祭坛，第 4 章催化剂和艾亭转职材料 / 支线故事圣女 3-7 / 紧急任务。
- **收获**：完成每日任务，圣域日常、月常收菜和智能托管，收取竞技场通行证奖励 / 临期邮件 / 宠物礼物。
- **召唤**：自动消耗每日免费抽卡次数、宠物小屋免费次数，~~(保存截图、识别召唤结果依然不准)~~。
- **竞技场**：刷竞技场人机，领取每周对战奖励。
- **商店**：自动购买每日 / 周商品、传承商店道具。
- **秘密商店**：自动刷取书签 / 神秘奖牌。
- **骑士团**：自动世界首领，团战出刀检测、提醒，自动捐赠花 / 企鹅，领取每周奖励，商店周常 / 月常商品购买。
- **后台托管**：自动启动模拟器和游戏，后台托管清体力和每日，通过仪表盘了解资源情况。
- **活动**：随缘更新。
- **工具**：提供各种辅助功能，如次元探查刷取、企鹅购买、传家宝辅助等，随缘更新。

## 运行进度与系统通知

桌面版在 Windows（视窗系统）任务栏显示本轮任务进度，浏览器在标签标题显示。进度按已处理任务数统计，不代表剩余时间；等待下一轮或停止时清除。

在通知设置中打开“系统通知”，即可与原有远程推送同时使用，不会覆盖远程配置。桌面版直接尝试系统通知；浏览器还需点击“启用系统通知”并允许权限，使用本机地址或加密连接，保持网页打开。可点击“发送测试通知”检查弹窗。独立命令行任务和网页服务需要使用同一个项目目录。

远程配置兼容原有单渠道写法，也可填写由多个渠道配置组成的列表，逐个发送；某一路失败不影响其他渠道。旧的 `provider: system`（系统渠道）配置会自动启用新开关，此后可单独关闭。提醒只要有一路接收就记录为已发送；系统通知排队成功不代表弹窗已显示。

仅在发生无法处理的异常或需要业务提醒时，按独立开关发送系统通知，同时尝试配置的远程渠道；任务执行结束时不提醒。多开界面时优先桌面版，每条只发给一个界面；没有可用界面时跳过，不影响任务。

## 安装

[设备支持文档](https://github.com/LmeSzinc/AzurLaneAutoScript/wiki/Emulator_cn)，支持 Windows / Mac / Linux 以及各种骚方式运行。

> **为什么使用模拟器？** 如果你用桌面端来运行脚本的话，游戏窗口必须保持在前台，我猜你也不想运行脚本的时候不能动鼠标键盘像个傻宝一样坐在那吧，所以用模拟器。

## 开发

- 建议阅读源码和历史提交。
- 欢迎提交 PR，挑选你感兴趣的部分进行开发即可。
- 已炼化出 skill，参考 [ALAS-Game-Porting-For-AutoEpicSeven](https://github.com/Rea1-ms/ALAS-Game-Porting-For-AutoEpicSeven)

> **如何添加多语言 / 多服务器支持？** 需要适配 assets，参考 [开发文档 “添加一个 Button” 一节](https://github.com/LmeSzinc/AzurLaneAutoScript/wiki/4.1.-Detection-objects#%E6%B7%BB%E5%8A%A0%E4%B8%80%E4%B8%AA-button)。

## 关于 Alas

AES 基于崩铁脚本 [StarRailCopilot](https://github.com/LmeSzinc/StarRailCopilot) 开发。


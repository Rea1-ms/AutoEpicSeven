# 上游同步记录

## 2026-09-27

本次对照[星铁助手主分支](https://github.com/LmeSzinc/StarRailCopilot/commit/b8e192c3)，选取 6 项通用模拟器修复。

| 更新 | 上游来源 |
| --- | --- |
| 修正复制双端口时的解析，并扩大多开端口配对范围 | [端口修复](https://github.com/LmeSzinc/StarRailCopilot/commit/928cd16d1bf8a743bd14635d7739e49db833b06b) |
| 多种模拟器共用端口时，优先按用户选择的模拟器类型定位 | [实例定位](https://github.com/LmeSzinc/StarRailCopilot/commit/8f134278d888c4a36ec763129dd0cda2a4b50aca) |
| 通过后台管理器启动多开实例；启动超时后重试，不再误报成功 | [启动修复](https://github.com/LmeSzinc/StarRailCopilot/commit/34e08cd8621f77f02c52509a46a362e565c12298) |
| 识别 MuMu（模拟器）安卓 15 实例名称 | [实例名称](https://github.com/LmeSzinc/StarRailCopilot/commit/a6a89a16f4fb0ecb1bd324451bafc3c7885e4e08) |
| 补充 MuMu（模拟器）6.0 的共享截图动态库路径 | [截图动态库](https://github.com/LmeSzinc/StarRailCopilot/commit/4d3a708402a410e4b1ee2425fa3a77a90833ea0c) |
| 安卓版本超出支持范围时，将 DroidCast（安卓截图组件）切换为自动选择截图方式 | [截图兼容性](https://github.com/LmeSzinc/StarRailCopilot/commit/27927ff0324fc5a35a87208ef39f3774a3cecb72) |

端口配对范围同时更新了连接层和模拟器发现层，避免两处判断不一致。保留已有输入修正规则、游戏包名与任务流程。

本轮未同步颜色阈值规则调整、文字提取优化、新增模拟器配置和星铁专用业务。这些改动需要另外核对现有任务的调用方式及截图样本。

验证：主检出目录的测试目录中新增 23 项离线回归测试，全部通过。覆盖旧版兼容、端口边界、多开定位、启动重试和截图方式选择；模拟器启动及动态库加载使用替身验证。静态检查没有新增问题，尚未进行实机验证。

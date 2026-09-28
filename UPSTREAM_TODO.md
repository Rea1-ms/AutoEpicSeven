# 上游改动检查与待办

更新日期：2026-09-28。首批 6 项已提交为 `a4f7917a`。本轮剩余 16 项已完成代码适配、配置生成和离线验证；通用清单累计 22 项已适配，另 1 项旧运行环境补丁不适用。

## 检查范围

- 共同祖先：`d2ef680e3f9d9878e126400a05b214660147e7dc`，2026-01-17。
- 上游截止：`b8e192c392ddaddf292c5f3583fcd90b8dc765e5`，2026-09-21。
- 本地状态核对到：`09824012` 之后的本轮工作树；保留已有业务修复。
- 按提交祖先关系检查共同祖先之后、上游截止点之前的全部 137 个提交，包括截止点，不按作者日期截断。部分回移提交的作者日期早于分叉点，仍已纳入。

| 分类 | 数量 | 现状 |
| --- | --- | --- |
| 有效通用改动 | 23 | 22 项已适配、1 项当前不适用 |
| 星铁专用改动 | 89 | 不合入；部分处理思路可参考 |
| 已被上游撤销的临时改动 | 1 | 不合入 |
| 合并提交 | 24 | 已检查合并差异，没有额外改动，不重复计算功能 |
| 合计 | 137 | 文末逐项列出，均可追溯到上游原始提交 |

“通用”指可用于第七史诗的框架、设备、部署或识别基础能力。不能仅凭文件位于基础目录就归为通用：服务器包名、副本排序、仪表盘项目等仍可能是星铁业务。混合提交按通用部分登记，星铁任务和资源不跟随导入。

检查了每个提交的文件清单、源码差异和后续修正关系；纯关键词、生成配置和图片提交核对了数据范围及资源定义，没有逐张目视验证星铁图片。待办状态是源码核对结果，不表示已经完成实机验证。

## 首批已合入的 6 项

以下均在 `a4f7917a` 中按本地结构移植，不是把整个上游提交直接合并。

| 编号 | 上游提交 | 改动 | 本地现状 |
| --- | --- | --- | --- |
| 通02 | `928cd16d` | 双端口输入解析及扩大多开端口配对范围 | 已合入；连接层和模拟器发现层的端口范围上界都从起始端口加 32 扩展为加 64，保留原有输入修正 |
| 通06 | `8f134278` | 同端口多个模拟器实例的选择 | 已合入；优先使用配置中的模拟器类型，再匹配旧名称、路径 |
| 通14 | `34e08cd8` | MuMu（模拟器）多开启动及失败重试 | 已合入；通过后台管理器启动，启动超时不再作为成功返回，最多执行 3 轮重试 |
| 通15 | `a6a89a16` | MuMu（模拟器）安卓 15 实例名 | 已合入；兼容国内与全球版名称 |
| 通16 | `4d3a7084` | MuMu（模拟器）6.0 截图动态库新位置 | 已合入；新旧查找位置都保留 |
| 通18 | `27927ff0` | DroidCast（安卓截图组件）的系统版本检查 | 已合入配置检查；本轮通07补齐两条测速入口，自动回退后不再重选不兼容截图方式 |

已有 23 项离线回归通过，覆盖端口解析与边界、实例定位、启动命令与重试、动态库查找、截图配置回退。模拟器及动态库使用替身验证，尚未进行实机验证。首批用例只覆盖配置回退；本轮正式测试补充了自动选择全过程。

## 本轮 16 项适配状态

按本地结构提取通用部分，没有整文件覆盖上游业务。这里的“已适配”指代码与离线回归完成，不代表对应平台已实机验收。

| 编号 | 上游提交 | 通用改动 | 当前状态与待办 |
| --- | --- | --- | --- |
| 通07 | `ff8ae76a` | 截图测速过滤安卓版本，计时改用单调时钟 | 已适配。完整测速和启动简化测速都过滤安卓版本界外的两种截图方式；计时使用单调高精度时钟。覆盖版本 22／23／32／33，以及配置回退后实际选出兼容候选。 |
| 通01 | `b52f77a8` | 网页静态文件类型表不受系统环境污染 | 已适配。网页启动重建内置文件类型表；验证系统错误关联不会污染脚本、样式和矢量图片类型。 |
| 通05 | `30f7ec81` | 替换 ADB（安卓调试工具）遇到权限错误时继续处理 | 已适配。移动或复制失败时记录错误并继续处理其他程序；复制失败会尝试恢复本次备份，恢复失败仍保留备份并记录位置。所有文件操作使用替身验证。 |
| 通10 | `63c08f59` | 分离命令警告与截图警告，兼容多屏提示新格式 | 已适配。独立处理命令与截图警告，覆盖新旧多屏提示、容器缓存提示及旧模拟器前缀；压缩图片和原始截图的解码入口均已验证。 |
| 通13 | `8e1a062f` | 清理 AMD（显卡厂商）在精简 Linux（操作系统）下的驱动警告 | 已适配。清理已知驱动警告两行，字节和文本输入均保留后续数据；未在安卓容器中实测。 |
| 通11 | `0c39eb99` | 前后缀工具的类型标注、命名和空后缀修复 | 已适配。补充类型标注和新名称，保留旧名称别名；空文本和空字节后缀不再把原内容清空。 |
| 通12 | `4045e1cf` | 依赖包信息补丁中的空后缀修复 | 已适配。依赖包补丁中的第二份后缀函数也修复空后缀，旧名称继续可用。 |
| 通04 | `26a864c5` | 文字识别结果批处理扩展和重新绑定关键词 | 已适配。增加识别结果批处理扩展点，保留原始结果供关键词重设；旧时长冒号格式和全角冒号处理已通过回归。 |
| 通08 | `07170b31` | 雷电 14 识别、共享截图和配置选项 | 已适配。雷电 14 类型、路径、注册表发现、共享截图和配置选项已补齐；经批准运行本地生成器，五种语言显示项完整，模板默认值不变，重复生成内容稳定。 |
| 通19 | `8fc79181` | 雷电 9／14 最小化启动 | 已适配。雷电 9／14 使用最小化启动参数，雷电 4 保持原命令；通08的配置生成已完成，命令构造通过模拟验证，尚未实机启动。 |
| 通17 | `beb4d216` | macOS（苹果操作系统）下 MuMu Pro（模拟器）的序列号识别 | 已适配。序列号结合引擎标记识别苹果平台的模拟器，并补齐保活和版本判断；仅使用属性替身验证，未在苹果平台实测。 |
| 通09 | `b0196092` | 下载加速服务支持多个地址，失败后回退 | 已适配通用多地址能力。请求错误、非成功状态或无效提交信息会继续下个地址；成功地址供后续包下载使用。保留第七史诗发布源和本地修改保护，未引入上游锁文件删除逻辑。实际仍只有原发布地址，尚无可靠备用源。 |
| 通20 | `de52c13b` | 颜色相似度、文字提取的分通道运算和缓冲复用 | 已适配并验证。采用分通道与缓冲复用，保留旧饱和加法和中间取整，避免阈值 300 的像素差异。69 张现有样本完成 690 次逐像素比较，结果一致；本机完整截图三个处理函数约快 1.42～3.45 倍，仅为函数测速。 |
| 通21 | `27893b7d` | 新增直接生成二值颜色掩码的工具 | 已适配。仅提取二值颜色掩码工具，覆盖大小图路径、饱和与容差边界；没有导入星铁助战、合成或小地图代码。 |
| 通22 | `1d75650d` | 颜色计数阈值由相似度改为色差容忍度 | 已适配。采用色差容差，默认 30；旧默认相似度 221 对应容差 34，两个默认值不等价。当前源码无业务直接调用，边界和像素数量严格比较已验证；没有恢复本地已移除的可拖动列表模块。 |
| 通23 | `f0fb197c` | 颜色按钮定位参数改名，并改用色差容忍度 | 已适配。新容差默认 5，包含边界；旧关键字 color_threshold（颜色相似度阈值）仍保留，并换算为严格整数边界。第三个位置参数现在表示色差容差，不是旧相似度；当前源码无业务直接调用，边界差异已有回归。 |

## 当前不适用及上游撤销项

| 编号／提交 | 处理结果 | 依据 |
| --- | --- | --- |
| 通03／`d4becd46` | 当前不合入，保留记录 | 补丁只在微软系统上的 Python（运行环境）3.7 启用；本项目要求 3.10。上游注释说明该问题在 3.8 及以后已经修复 |
| `02a62630` | 不合入 | 临时关闭共享内存截图优先选择；`ac11f8d0` 随后原样恢复。后者剩余的材料名称纠错属于星铁专用；本地当前保留截图优先选择 |

## 验收结果与后续待办

- 本轮新增 30 项正式回归，已纳入统一入口与分类清单；默认 118 项全部通过，零跳过，耗时 59.7 秒。缺模型、故意断言失败和误建真实设备三类负向检查均符合预期。
- 69 张现有截图用于颜色处理对照；相似度、白字及彩色字提取在常用阈值和扩展阈值 300 下完成 690 次逐像素比较，与本轮修改前一致。完整截图的本机函数耗时分别约为原来的 43%、29%、70%；不能推算为整个任务等比例提速。
- 雷电配置已生成并核对：选项、五种语言文字齐全，原模板默认值不变，重复生成内容稳定。模拟器启动、截图动态库、苹果平台和安卓容器尚需对应环境手动验收。
- 下载多地址机制已有离线验证；实际备用发布源尚未提供，不能宣称线上已实现多源容灾。
- 新颜色计数的第三个参数、新颜色按钮的第三个位置参数均按色差容差解释。按钮保留旧关键字兼容；模板匹配相似度没有改动。将来新增调用时不能照搬旧相似度数值。
- 本轮没有新增或修改资产，不需要补截图。测试产物保存在被忽略的 screenshots/offline_test_results（离线测试结果）目录，没有自动删除；按需由用户清理。

## 保留的本地修复

本地 `7c4633aa` 已修正模板匹配参数顺序，并统一骑士团队徽匹配阈值。上游本次范围没有修复相同的等尺寸黑图误判；以后同步基础图像代码时应保留该修复。上游头像匹配等任务仍有不同实现，不作为覆盖本地代码的依据。

本地文字时长识别、导航和截图保存目录也有独立修正。本清单只登记上游通用增量，不建议整文件覆盖。

## 星铁业务中可借鉴的思路

以下不属于可直接移植的通用补丁，也不计入上面的 16 项通用适配。只有第七史诗出现相应问题时，再结合自己的页面和截图实现。

| 上游提交 | 可借鉴内容 | 当前处理 |
| --- | --- | --- |
| `86f3fa7b` | 多层循环中确认退出的是目标状态循环 | 星铁助战修正；当前不移植 |
| `e904864f`、`de1260b6`、`966983e8` | 图片、红点和页面渐入期间有限等待，避免把未加载当作不存在 | 依赖各自页面；当前不移植 |
| `0903bfb4` | 等待页面位置稳定后继续识别 | 星铁个人资料页面；当前不移植 |
| `3dfcffbf`、`26d63ab1` | 颜色判断结合模板或文字结果，降低单一颜色误判 | 星铁合成稀有度规则；当前不移植 |

## 逐个提交检查记录

按祖先先于后代的顺序列出全部 137 个提交。日期使用提交日期；来源链接对应完整提交编号。“不合入”说明不属于本次第七史诗通用同步，不评价上游改动本身的价值。

| 序号 | 提交日期 | 上游来源 | 分类／状态 | 核对结论 |
| --- | --- | --- | --- | --- |
| 1 | 2026-02-06 | [ef4f318b](https://github.com/LmeSzinc/StarRailCopilot/commit/ef4f318b62cb4d51b469084d48a95bef37545a9c) | 星铁专用／不合入 | 越南官服包名、启动入口、养成扫描语言和材料纠错；均依赖星铁 |
| 2 | 2026-02-06 | [4be6da75](https://github.com/LmeSzinc/StarRailCopilot/commit/4be6da75dc766b2e75b9d29f1edf9f7d997b624c) | 星铁专用／不合入 | 4.0 兑换码与到期时间 |
| 3 | 2026-02-06 | [e0be2276](https://github.com/LmeSzinc/StarRailCopilot/commit/e0be2276c36235bcf8cacf036c0ff44c3f4e81f3) | 星铁专用／不合入 | 撤回越南服提交中错误的星铁包名前缀推断；第七史诗使用独立映射 |
| 4 | 2026-02-06 | [fe8b1395](https://github.com/LmeSzinc/StarRailCopilot/commit/fe8b1395c5c71795724c96e0af3dd6fe2495ff2d) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 5 | 2026-02-18 | [43b228a3](https://github.com/LmeSzinc/StarRailCopilot/commit/43b228a3c4b6e9e747b9bcfd71a73a05a702f382) | 星铁专用／不合入 | 4.0 关键词、二相乐园、委托任务名称和模拟宇宙事件 |
| 6 | 2026-02-18 | [514da73d](https://github.com/LmeSzinc/StarRailCopilot/commit/514da73dd49b2a7f4450edee8732102b0632648d) | 星铁专用／不合入 | 4.0 角色、材料、关卡及越南服的生成配置 |
| 7 | 2026-02-18 | [bfb71918](https://github.com/LmeSzinc/StarRailCopilot/commit/bfb7191831bf86a3d53959827555d71be949cd7d) | 星铁专用／不合入 | 二相乐园登录广告，依赖星铁模板 |
| 8 | 2026-02-18 | [c6b6b815](https://github.com/LmeSzinc/StarRailCopilot/commit/c6b6b8158215d12c6f87f159cf79d8b99a3d2de1) | 星铁专用／不合入 | 4.0 委托状态流程及教程弹窗抽取；仍依赖星铁教程模板 |
| 9 | 2026-02-19 | [7f8030ee](https://github.com/LmeSzinc/StarRailCopilot/commit/7f8030eee094a1d391eabca0bd8e987c7f9f02b4) | 星铁专用／不合入 | 助战页签改为图鉴、支援、队伍，并处理仅好友选项 |
| 10 | 2026-02-19 | [0f62fe0f](https://github.com/LmeSzinc/StarRailCopilot/commit/0f62fe0f0b681a8a412a1b14d865ccd884e3c5fc) | 星铁专用／不合入 | 新版助战头像匹配、替换队员、滚动搜索和确认流程 |
| 11 | 2026-02-19 | [b52f77a8](https://github.com/LmeSzinc/StarRailCopilot/commit/b52f77a81f37e85b04b28add1cfe05d525e5a1f3) | 通01／已适配 | 网页静态文件类型表不受系统环境污染 |
| 12 | 2026-02-19 | [928cd16d](https://github.com/LmeSzinc/StarRailCopilot/commit/928cd16d1bf8a743bd14635d7739e49db833b06b) | 通02／已合入 | 双端口输入解析及扩大多开端口配对范围 |
| 13 | 2026-02-19 | [d4becd46](https://github.com/LmeSzinc/StarRailCopilot/commit/d4becd46f0ddcf812fc3da423e4bb2c018a2025b) | 通03／当前不适用 | 仅微软系统上的旧版运行环境 3.7 需要该子进程通信补丁 |
| 14 | 2026-02-19 | [115474a3](https://github.com/LmeSzinc/StarRailCopilot/commit/115474a39a1177926aa0363b2c95077d903c2511) | 星铁专用／不合入 | 二相乐园副本排序及欢愉命途配置；未改变通用配置机制 |
| 15 | 2026-02-19 | [6b6820c1](https://github.com/LmeSzinc/StarRailCopilot/commit/6b6820c13f8c55edf4de409dd5c60f51fccf2f9c) | 星铁专用／不合入 | 助战模板采集脚本适配新列表和关卡 |
| 16 | 2026-02-19 | [da44c9e0](https://github.com/LmeSzinc/StarRailCopilot/commit/da44c9e0ba769801ad0496fdcd1ef87756f2a135) | 星铁专用／不合入 | 角色头像资源及皮肤映射 |
| 17 | 2026-02-19 | [84dcc32c](https://github.com/LmeSzinc/StarRailCopilot/commit/84dcc32c271ae1f686b4ddfefdc603dae4dee611) | 星铁专用／不合入 | 助战替换第几位队员的配置和调用 |
| 18 | 2026-02-19 | [1e5b9acc](https://github.com/LmeSzinc/StarRailCopilot/commit/1e5b9acc5619189434b55e012fdbe21d795247ca) | 星铁专用／不合入 | 饰品助战复用普通助战流程，并修正皮肤截图变量 |
| 19 | 2026-02-19 | [2f875383](https://github.com/LmeSzinc/StarRailCopilot/commit/2f8753839adaa411d2ff442da0e5d190875781a5) | 星铁专用／不合入 | 饰品队伍设置改为可沿用上次队伍 |
| 20 | 2026-02-19 | [3807461e](https://github.com/LmeSzinc/StarRailCopilot/commit/3807461ed26882891bce769f365fa664ace8675d) | 星铁专用／不合入 | 饰品队伍列表识别、选择及副本拖动区域 |
| 21 | 2026-02-19 | [d0217020](https://github.com/LmeSzinc/StarRailCopilot/commit/d0217020573e087e67704cbe659af7d5dc1901ca) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 22 | 2026-02-19 | [5978b10d](https://github.com/LmeSzinc/StarRailCopilot/commit/5978b10db2f30854592438470c4b3f144cd01db3) | 星铁专用／不合入 | 饰品提取帮助说明改为每月存档要求 |
| 23 | 2026-03-05 | [6436e59b](https://github.com/LmeSzinc/StarRailCopilot/commit/6436e59bd6c4bf1238d49c9dfe2c6198076a2eee) | 星铁专用／不合入 | 英文毛茸茸材料名称截断识别修正 |
| 24 | 2026-03-05 | [ef68aa85](https://github.com/LmeSzinc/StarRailCopilot/commit/ef68aa85bd5eba4e74dee7af1822b9aafbe7fc64) | 星铁专用／不合入 | 用助战页签区分饰品与普通副本 |
| 25 | 2026-03-05 | [46da4287](https://github.com/LmeSzinc/StarRailCopilot/commit/46da428772e5b32b335b20eef05d73a34223df4f) | 星铁专用／不合入 | 角色头像资源 |
| 26 | 2026-03-06 | [a8a96bec](https://github.com/LmeSzinc/StarRailCopilot/commit/a8a96bec8d807e5d4183bec1cc510dc53013c5a0) | 星铁专用／不合入 | 结合地点与副本名称区分二相乐园金色拟造花萼；依赖通04 |
| 27 | 2026-03-06 | [757916f4](https://github.com/LmeSzinc/StarRailCopilot/commit/757916f480c8c246aaf1bd8e5ea14b80b5001562) | 星铁专用／不合入 | 星铁登录流程增加账号确认处理 |
| 28 | 2026-03-06 | [bf94e99b](https://github.com/LmeSzinc/StarRailCopilot/commit/bf94e99be8fcd9e60d8cb396f7ea9963ff1aa098) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 29 | 2026-03-06 | [26a864c5](https://github.com/LmeSzinc/StarRailCopilot/commit/26a864c5677e8c36813ff36226b1cade25b54e3f) | 通04／已适配 | 文字识别结果批处理扩展和重新绑定关键词 |
| 30 | 2026-03-06 | [5d751eec](https://github.com/LmeSzinc/StarRailCopilot/commit/5d751eec8aab6bb6be1e530f0996e100dc58fb46) | 星铁专用／不合入 | 助战入场同时检查页签和刷新按钮；循环退出随后由 86f3fa7b 修正 |
| 31 | 2026-03-06 | [f071f89e](https://github.com/LmeSzinc/StarRailCopilot/commit/f071f89e9373007d2bfb6871a90b80ec0e2861bc) | 星铁专用／不合入 | 星铁日常合成改用亮度匹配及已选状态判断 |
| 32 | 2026-03-06 | [30dfc0ab](https://github.com/LmeSzinc/StarRailCopilot/commit/30dfc0ab156863ca4189fc97c5a7b05d5dd28056) | 星铁专用／不合入 | 星铁日常合成允许任一候选物品处于选中状态 |
| 33 | 2026-03-06 | [cbb0a991](https://github.com/LmeSzinc/StarRailCopilot/commit/cbb0a9917c40de49caa32a96ff9fb952265b19b3) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 34 | 2026-03-06 | [86f3fa7b](https://github.com/LmeSzinc/StarRailCopilot/commit/86f3fa7b215d43ff00e7ed5c49c135f30c49d607) | 星铁专用／不合入 | 修正助战内层循环退出目标；未修改基础循环实现 |
| 35 | 2026-03-06 | [141627de](https://github.com/LmeSzinc/StarRailCopilot/commit/141627de8fa0c023d32180924b69c414e867a479) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 36 | 2026-03-13 | [e72ab72f](https://github.com/LmeSzinc/StarRailCopilot/commit/e72ab72f55603fd41cfeed08a05505c9beefdaf2) | 星铁专用／不合入 | 4.1 兑换码与到期时间 |
| 37 | 2026-03-23 | [eeb8a278](https://github.com/LmeSzinc/StarRailCopilot/commit/eeb8a278d3e491f8bd3e127ac4858cb505ed0813) | 星铁专用／不合入 | 天谴血矛材料名称纠错 |
| 38 | 2026-03-26 | [82e52b48](https://github.com/LmeSzinc/StarRailCopilot/commit/82e52b483a2c8f39598e0d5760ab836c7cda21a0) | 星铁专用／不合入 | 新增匹诺康尼饰品战斗路线 |
| 39 | 2026-03-26 | [377d4c08](https://github.com/LmeSzinc/StarRailCopilot/commit/377d4c081a12d948a65c639ff44b88717cc25993) | 星铁专用／不合入 | 4.1 角色、光锥、关卡及事件关键词 |
| 40 | 2026-03-26 | [d2dc88e8](https://github.com/LmeSzinc/StarRailCopilot/commit/d2dc88e8e7986aaeae1a86ee2255ff3ac7f38d0e) | 星铁专用／不合入 | 4.1 生成配置和角色版本 |
| 41 | 2026-03-26 | [f361bd47](https://github.com/LmeSzinc/StarRailCopilot/commit/f361bd47730a65a97c91fc6a8bfd0bb225c5bfb9) | 星铁专用／不合入 | 角色头像资源 |
| 42 | 2026-03-26 | [c1415a6d](https://github.com/LmeSzinc/StarRailCopilot/commit/c1415a6dcff4597af6a791dd23a00a2d60a9ad6d) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 43 | 2026-03-28 | [3702764d](https://github.com/LmeSzinc/StarRailCopilot/commit/3702764d8355b0194fc8971dd35daf3e7670cb76) | 星铁专用／不合入 | 差分宇宙页面增加模板变体 |
| 44 | 2026-03-28 | [9bd3f49c](https://github.com/LmeSzinc/StarRailCopilot/commit/9bd3f49c9899be136179d99ce06d8ac29a8a658a) | 星铁专用／不合入 | 遗器与饰品简称及翻译 |
| 45 | 2026-03-28 | [31564a7b](https://github.com/LmeSzinc/StarRailCopilot/commit/31564a7bb48568b25c19a9a1ae2a323513b02cd9) | 星铁专用／不合入 | 移除 4.1 已不需要的饰品存档限制说明 |
| 46 | 2026-03-28 | [3592893d](https://github.com/LmeSzinc/StarRailCopilot/commit/3592893d4c5ecb1e26272ad74020823e4a527839) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 47 | 2026-03-28 | [30f7ec81](https://github.com/LmeSzinc/StarRailCopilot/commit/30f7ec8111ff30ed0f6817f56018494ebc2ab9d4) | 通05／已适配 | 替换 ADB（安卓调试工具）遇到权限错误时继续处理 |
| 48 | 2026-03-30 | [4b203788](https://github.com/LmeSzinc/StarRailCopilot/commit/4b20378818c119a0ed1ca71cec3303f3ac7864dd) | 星铁专用／不合入 | 模拟宇宙周积分分母被截断的识别修正 |
| 49 | 2026-03-30 | [31c31e45](https://github.com/LmeSzinc/StarRailCopilot/commit/31c31e4506ffb7ec2c7cad9baf5fb0a32df172ad) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 50 | 2026-04-01 | [8f134278](https://github.com/LmeSzinc/StarRailCopilot/commit/8f134278d888c4a36ec763129dd0cda2a4b50aca) | 通06／已合入 | 同端口多个模拟器实例的选择 |
| 51 | 2026-04-01 | [ff8ae76a](https://github.com/LmeSzinc/StarRailCopilot/commit/ff8ae76a17acb21bff7af0f67628902bf78e77b0) | 通07／已适配 | 截图测速过滤安卓版本，计时改用单调时钟 |
| 52 | 2026-04-01 | [67b586d7](https://github.com/LmeSzinc/StarRailCopilot/commit/67b586d7d327cb919fe0b1a7d83eb73e2998fd4a) | 星铁专用／不合入 | 不可知域副本关键词生成 |
| 53 | 2026-04-01 | [e5a7aee9](https://github.com/LmeSzinc/StarRailCopilot/commit/e5a7aee9c112b5afcd52b9e76999332c7273eacc) | 星铁专用／不合入 | 从各扩展主题切换回模拟宇宙 |
| 54 | 2026-04-01 | [403ba1ab](https://github.com/LmeSzinc/StarRailCopilot/commit/403ba1abf939a6a1a9d32126bf225a0e58c7c552) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 55 | 2026-04-10 | [33dd1858](https://github.com/LmeSzinc/StarRailCopilot/commit/33dd18583a51ed14d57e4f33c96b88544704dad6) | 星铁专用／不合入 | 4.2 兑换码与到期时间 |
| 56 | 2026-04-15 | [687a3814](https://github.com/LmeSzinc/StarRailCopilot/commit/687a3814db532fa74f764e175570d17aa19b615a) | 星铁专用／不合入 | 模拟宇宙奖励红点扩大识别区域 |
| 57 | 2026-04-15 | [9a23825f](https://github.com/LmeSzinc/StarRailCopilot/commit/9a23825f1c55f0f0a9f3afa5c4fddd4bea97f7ed) | 星铁专用／不合入 | 凝滞虚影等关卡中文识别纠错 |
| 58 | 2026-04-15 | [c211d2d6](https://github.com/LmeSzinc/StarRailCopilot/commit/c211d2d6143637a25c892c4bcb244d6b29a294c6) | 星铁专用／不合入 | 通过星铁副本分类按钮确认生存索引加载完成 |
| 59 | 2026-04-15 | [a78e20a0](https://github.com/LmeSzinc/StarRailCopilot/commit/a78e20a0fec1ff45ac96117e71b21c39b1c65772) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 60 | 2026-04-20 | [9570646e](https://github.com/LmeSzinc/StarRailCopilot/commit/9570646e47d95a627e3959c4308216e2093ab1bb) | 星铁专用／不合入 | 4.2 模拟宇宙奖励改为通知后的页面和积分处理 |
| 61 | 2026-04-22 | [b605dba7](https://github.com/LmeSzinc/StarRailCopilot/commit/b605dba74b6d5ed1ac0c1ebc7a06672c9bc62170) | 星铁专用／不合入 | 4.2 角色、材料、关卡及任务关键词 |
| 62 | 2026-04-23 | [77d045ba](https://github.com/LmeSzinc/StarRailCopilot/commit/77d045bad1950f5217fd73b3d6cd0b8c2031a936) | 星铁专用／不合入 | 4.2 生成配置和角色版本 |
| 63 | 2026-04-23 | [af5c1406](https://github.com/LmeSzinc/StarRailCopilot/commit/af5c14062550200482f9137ea374d273d21ef220) | 星铁专用／不合入 | 英文副本地点的分段识别合并 |
| 64 | 2026-04-23 | [f8ed5c8d](https://github.com/LmeSzinc/StarRailCopilot/commit/f8ed5c8de2ea647f72243978f06d64f920a26830) | 星铁专用／不合入 | 关闭银狼新版本登录广告 |
| 65 | 2026-04-23 | [d7434a43](https://github.com/LmeSzinc/StarRailCopilot/commit/d7434a436a6e3853816cb135b872e6c3a60c5cae) | 星铁专用／不合入 | 登录确认模板改识别设置按钮；基础页面文件仅补注释 |
| 66 | 2026-04-23 | [33eb1afe](https://github.com/LmeSzinc/StarRailCopilot/commit/33eb1afe79d4bd9542eabdec1861b2eedb687e71) | 星铁专用／不合入 | 差分宇宙进入模拟宇宙的按钮与点击区域 |
| 67 | 2026-04-23 | [b92c711d](https://github.com/LmeSzinc/StarRailCopilot/commit/b92c711dbb416296f5808e168f68537d23aa1ef6) | 星铁专用／不合入 | 模拟宇宙周奖励页面模板 |
| 68 | 2026-04-23 | [e0ff6add](https://github.com/LmeSzinc/StarRailCopilot/commit/e0ff6add34de0ac933a3e64fa55a4b29571f00dc) | 星铁专用／不合入 | 模拟宇宙周积分上限改为 18000 及识别修正 |
| 69 | 2026-04-23 | [0903bfb4](https://github.com/LmeSzinc/StarRailCopilot/commit/0903bfb4a26a90c4e5a25b9240af592eb26d252b) | 星铁专用／不合入 | 个人资料展示切换、页面稳定等待与助战收益领取 |
| 70 | 2026-04-23 | [3dfcffbf](https://github.com/LmeSzinc/StarRailCopilot/commit/3dfcffbfb04e61835919d379b5d0f81984c6958b) | 星铁专用／不合入 | 星铁合成稀有度判断增加物品数量模板，避免蓝背景误判 |
| 71 | 2026-04-23 | [7b950499](https://github.com/LmeSzinc/StarRailCopilot/commit/7b950499d42bfb3b4104fb5d80ad3ed44ac43d20) | 星铁专用／不合入 | 助战角色头像和皮肤资源 |
| 72 | 2026-04-23 | [e128f36f](https://github.com/LmeSzinc/StarRailCopilot/commit/e128f36f10bbbdf3f961e16b9ae45edbf02a6a12) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 73 | 2026-05-11 | [07170b31](https://github.com/LmeSzinc/StarRailCopilot/commit/07170b317993d9af5e9d67fb59dc8597f837741e) | 通08／已适配 | 雷电 14 识别、共享截图和配置选项 |
| 74 | 2026-05-14 | [2436c48f](https://github.com/LmeSzinc/StarRailCopilot/commit/2436c48f277bf9e87dc5dd5ee8542b29fd3838b1) | 星铁专用／不合入 | 模拟宇宙领奖时处理燃料券放弃提示 |
| 75 | 2026-05-14 | [15189607](https://github.com/LmeSzinc/StarRailCopilot/commit/1518960777d497279b2bc935cbc2edc4e48e0983) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 76 | 2026-05-14 | [b0196092](https://github.com/LmeSzinc/StarRailCopilot/commit/b0196092f49ec2bd00903f279a227bbb2e8b9a48) | 通09／已适配 | 下载加速服务支持多个地址，失败后回退 |
| 77 | 2026-05-18 | [e904864f](https://github.com/LmeSzinc/StarRailCopilot/commit/e904864f24aa311575bdd9c67c779b3e094eae38) | 星铁专用／不合入 | 助战头像首次加载时增加有限等待 |
| 78 | 2026-05-19 | [c0ef04b0](https://github.com/LmeSzinc/StarRailCopilot/commit/c0ef04b00b5c55efe1145d5c3605cceb5baaff27) | 星铁专用／不合入 | 修正模拟宇宙领奖调用的多余弹窗参数；未改通用弹窗接口 |
| 79 | 2026-05-19 | [de1260b6](https://github.com/LmeSzinc/StarRailCopilot/commit/de1260b6d9ca47379a68f3957fde50b21a4c3d45) | 星铁专用／不合入 | 星铁邮件红点透明动画期间有限等待 |
| 80 | 2026-05-19 | [7c1cae50](https://github.com/LmeSzinc/StarRailCopilot/commit/7c1cae5029cc9fa8c5dc37e1c32fc879bc4bd248) | 星铁专用／不合入 | 增加不参加双倍副本活动的配置值和分支 |
| 81 | 2026-05-19 | [c33738cf](https://github.com/LmeSzinc/StarRailCopilot/commit/c33738cffd62798f5afdfa06d6836a92764fb9d1) | 星铁专用／不合入 | 角色头像资源 |
| 82 | 2026-05-19 | [10157beb](https://github.com/LmeSzinc/StarRailCopilot/commit/10157beb3bc9fc2dafe25891bf1f96edb6f13ed0) | 星铁专用／不合入 | 三周年双倍花萼图标颜色适配 |
| 83 | 2026-05-19 | [80bc255b](https://github.com/LmeSzinc/StarRailCopilot/commit/80bc255ba0b0a2f5545abacc5a599733c1b5312d) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 84 | 2026-05-20 | [0a34dc57](https://github.com/LmeSzinc/StarRailCopilot/commit/0a34dc5780c847b3d28219b42705d560d0cc9a76) | 星铁专用／不合入 | 饰品流程重入时识别已在战斗中 |
| 85 | 2026-05-20 | [e229ab5f](https://github.com/LmeSzinc/StarRailCopilot/commit/e229ab5f828301e2863b27c1274659ee6dedf2a9) | 星铁专用／不合入 | 模拟宇宙奖励通知时延后任务，避免误记积分已满 |
| 86 | 2026-05-20 | [76a2e5b6](https://github.com/LmeSzinc/StarRailCopilot/commit/76a2e5b6808e581e19053e4e866bc0e15617e917) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 87 | 2026-05-22 | [2b68cf12](https://github.com/LmeSzinc/StarRailCopilot/commit/2b68cf120da6e8ecb8da6018ff4c3f2280c2d002) | 星铁专用／不合入 | 4.3 兑换码与到期时间 |
| 88 | 2026-05-22 | [63c08f59](https://github.com/LmeSzinc/StarRailCopilot/commit/63c08f59dc75dc04de6db946451367a9657281a4) | 通10／已适配 | 分离命令警告与截图警告，兼容多屏提示新格式 |
| 89 | 2026-05-22 | [0c39eb99](https://github.com/LmeSzinc/StarRailCopilot/commit/0c39eb999b0572ed501b34e0baf2ad49fe6e9215) | 通11／已适配 | 前后缀工具的类型标注、命名和空后缀修复 |
| 90 | 2026-05-22 | [4045e1cf](https://github.com/LmeSzinc/StarRailCopilot/commit/4045e1cf7651a6c9bee96d6582b6442e0aaed683) | 通12／已适配 | 依赖包信息补丁中的空后缀修复 |
| 91 | 2026-05-22 | [8ed38278](https://github.com/LmeSzinc/StarRailCopilot/commit/8ed3827833c0052a873676a98c7e24d425d623c8) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 92 | 2026-06-03 | [e103fb15](https://github.com/LmeSzinc/StarRailCopilot/commit/e103fb15cdf905cb4d21ec6c26b34f873413f994) | 星铁专用／不合入 | 4.3 关键词、角色名称映射及不可刷材料排除 |
| 93 | 2026-06-03 | [e29d6d51](https://github.com/LmeSzinc/StarRailCopilot/commit/e29d6d5154ce9e23b9fda14a5a10823d5d574f99) | 星铁专用／不合入 | 4.3 生成配置和角色版本 |
| 94 | 2026-06-03 | [86c4bfce](https://github.com/LmeSzinc/StarRailCopilot/commit/86c4bfce8cad940899233df41b7fa2b92caae949) | 星铁专用／不合入 | 燃料券放弃界面存在时暂不点击普通确认 |
| 95 | 2026-06-03 | [9d0847c0](https://github.com/LmeSzinc/StarRailCopilot/commit/9d0847c040f846b84a2da09fc3cd2837397780ac) | 星铁专用／不合入 | 角色头像资源 |
| 96 | 2026-06-03 | [8ceaf2ca](https://github.com/LmeSzinc/StarRailCopilot/commit/8ceaf2cacc52be830bd28b22d17f5de7351f86f0) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 97 | 2026-06-11 | [26d63ab1](https://github.com/LmeSzinc/StarRailCopilot/commit/26d63ab1f5431a5ddaa9d8b53a8ae0045fe426e2) | 星铁专用／不合入 | 星铁合成页通过物品名称补充稀有度判断 |
| 98 | 2026-06-11 | [8e1a062f](https://github.com/LmeSzinc/StarRailCopilot/commit/8e1a062fe101f9f426852ecb41e6ade21f5676fb) | 通13／已适配 | 清理 AMD（显卡厂商）在精简 Linux（操作系统）下的驱动警告 |
| 99 | 2026-06-11 | [c0c4cc9c](https://github.com/LmeSzinc/StarRailCopilot/commit/c0c4cc9c307f7b3fc84f1f20548d37147e4f47a7) | 星铁专用／不合入 | 记录合成物品稀有度的文字识别结果 |
| 100 | 2026-06-11 | [8ea86886](https://github.com/LmeSzinc/StarRailCopilot/commit/8ea86886ff1b569bc2d134e6e5dcc2ac975fefcc) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 101 | 2026-06-12 | [34e08cd8](https://github.com/LmeSzinc/StarRailCopilot/commit/34e08cd8621f77f02c52509a46a362e565c12298) | 通14／已合入 | MuMu（模拟器）多开启动及失败重试 |
| 102 | 2026-06-18 | [fb327a4b](https://github.com/LmeSzinc/StarRailCopilot/commit/fb327a4bd37906b394ee26f93aad2654a58be7c5) | 星铁专用／不合入 | 补充星铁队伍页签背景模板，防止来回切换 |
| 103 | 2026-07-02 | [a6a89a16](https://github.com/LmeSzinc/StarRailCopilot/commit/a6a89a16f4fb0ecb1bd324451bafc3c7885e4e08) | 通15／已合入 | MuMu（模拟器）安卓 15 实例名 |
| 104 | 2026-07-03 | [32ea4cc3](https://github.com/LmeSzinc/StarRailCopilot/commit/32ea4cc3bd605b8bb4b781e68d347faf356b98f0) | 星铁专用／不合入 | 4.4 兑换码与到期时间 |
| 105 | 2026-07-04 | [4d3a7084](https://github.com/LmeSzinc/StarRailCopilot/commit/4d3a708402a410e4b1ee2425fa3a77a90833ea0c) | 通16／已合入 | MuMu（模拟器）6.0 截图动态库新位置 |
| 106 | 2026-07-18 | [6dd79c90](https://github.com/LmeSzinc/StarRailCopilot/commit/6dd79c903697f21901b22b20012c131710ee6cfb) | 星铁专用／不合入 | 4.4 关键词、周本及饰品套装到关卡的数据映射 |
| 107 | 2026-07-18 | [677d1315](https://github.com/LmeSzinc/StarRailCopilot/commit/677d1315440e591c4f104a2590ebd17e7e3cfe35) | 星铁专用／不合入 | 4.4 生成配置和角色版本 |
| 108 | 2026-07-18 | [beb4d216](https://github.com/LmeSzinc/StarRailCopilot/commit/beb4d21614ef644b3deb010c80d12b6e04f4c6c2) | 通17／已适配 | macOS（苹果操作系统）下 MuMu Pro（模拟器）的序列号识别 |
| 109 | 2026-07-18 | [7c6c0991](https://github.com/LmeSzinc/StarRailCopilot/commit/7c6c09917a0b4d033a6813eb00e6f511012d2605) | 星铁专用／不合入 | 新增姬子版本登录广告处理，停用旧广告分支 |
| 110 | 2026-07-18 | [f3b367e5](https://github.com/LmeSzinc/StarRailCopilot/commit/f3b367e506360d309688d13bc67b2db94a0b4174) | 星铁专用／不合入 | 角色头像资源 |
| 111 | 2026-07-18 | [27927ff0](https://github.com/LmeSzinc/StarRailCopilot/commit/27927ff0324fc5a35a87208ef39f3774a3cecb72) | 通18／已合入，配套待补 | DroidCast（安卓截图组件）的系统版本检查 |
| 112 | 2026-07-18 | [55eaf50b](https://github.com/LmeSzinc/StarRailCopilot/commit/55eaf50be6c095762662f529e291bb92489ef5b0) | 星铁专用／不合入 | 角色选项中将姬子新版本前移 |
| 113 | 2026-07-18 | [0f2aaf8c](https://github.com/LmeSzinc/StarRailCopilot/commit/0f2aaf8c86772186e93bca830c998c5ddac12758) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 114 | 2026-08-04 | [02a62630](https://github.com/LmeSzinc/StarRailCopilot/commit/02a62630c4fd28507385bb16ea6e7fa02fcf2a9f) | 上游已撤销／不合入 | 曾关闭共享内存截图优先选择，已被 ac11f8d0 原样撤回 |
| 115 | 2026-08-04 | [67e12178](https://github.com/LmeSzinc/StarRailCopilot/commit/67e121782466372973811b337884e19562fc3da2) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 116 | 2026-08-04 | [ac11f8d0](https://github.com/LmeSzinc/StarRailCopilot/commit/ac11f8d0a6184dfb1c38ff015af369161646c057) | 星铁专用／不合入 | 撤回 02a62630 的截图改动，并加入嗤笑丑面材料识别纠错 |
| 117 | 2026-08-14 | [b9f7d10f](https://github.com/LmeSzinc/StarRailCopilot/commit/b9f7d10f193a237741807331e56d290d812509d4) | 星铁专用／不合入 | 4.5 兑换码与到期时间 |
| 118 | 2026-08-14 | [8fc79181](https://github.com/LmeSzinc/StarRailCopilot/commit/8fc79181a002bb7356b82c5542ac525b679ae91c) | 通19／已适配 | 雷电 9／14 最小化启动 |
| 119 | 2026-08-14 | [e3d2e809](https://github.com/LmeSzinc/StarRailCopilot/commit/e3d2e809278c6197bb585037397dba9db07c22cc) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 120 | 2026-08-27 | [d343740d](https://github.com/LmeSzinc/StarRailCopilot/commit/d343740d1f6f9d5990d0753156994bd89bdbb435) | 星铁专用／不合入 | 4.5 角色、光锥、关卡及事件关键词 |
| 121 | 2026-08-27 | [bcd7375f](https://github.com/LmeSzinc/StarRailCopilot/commit/bcd7375f5c6a673a750e27076bbc78cb4b20a00b) | 星铁专用／不合入 | 4.5 生成配置和角色版本 |
| 122 | 2026-08-27 | [8c90d04d](https://github.com/LmeSzinc/StarRailCopilot/commit/8c90d04d9472ea14c4cb35edb1eef4434512e30f) | 星铁专用／不合入 | 4.5 角色与皮肤头像资源 |
| 123 | 2026-08-27 | [4163107b](https://github.com/LmeSzinc/StarRailCopilot/commit/4163107b2cc50163997e9f2fd7e9fd889ac9c294) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 124 | 2026-09-01 | [2fb1de14](https://github.com/LmeSzinc/StarRailCopilot/commit/2fb1de14b0fb45b4b262ef91c88cb7690ddd6faf) | 星铁专用／不合入 | 新增星轨专票数量记录、识别模板和仪表盘字段 |
| 125 | 2026-09-10 | [de52c13b](https://github.com/LmeSzinc/StarRailCopilot/commit/de52c13b4c12ba3e245fe897f89cd4dd84090935) | 通20／已适配 | 颜色相似度、文字提取的分通道运算和缓冲复用 |
| 126 | 2026-09-10 | [75db3959](https://github.com/LmeSzinc/StarRailCopilot/commit/75db3959c93158267125a64ce8d4b51fd6ca00ce) | 星铁专用／不合入 | 移出模拟宇宙仪表盘项并迁移其存储路径；没有修改通用仪表盘布局 |
| 127 | 2026-09-10 | [933cc36b](https://github.com/LmeSzinc/StarRailCopilot/commit/933cc36bbd8fd190729c1bce9c081ae5bb8a77a6) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 128 | 2026-09-10 | [4e8f5831](https://github.com/LmeSzinc/StarRailCopilot/commit/4e8f583174ec99adaa59d9b54782be30792e4ca5) | 星铁专用／不合入 | 星轨专票允许数量为零并处理非数字文本 |
| 129 | 2026-09-11 | [966983e8](https://github.com/LmeSzinc/StarRailCopilot/commit/966983e821ae7f076fbbde43412e8a26e42bb070) | 星铁专用／不合入 | 星轨专票识别前等待跃迁页面加载 |
| 130 | 2026-09-12 | [c3c34738](https://github.com/LmeSzinc/StarRailCopilot/commit/c3c3473824ff932411b7a114212f0889aa48e4ef) | 星铁专用／不合入 | 扩大星铁剧情跳过按钮搜索区域 |
| 131 | 2026-09-12 | [27893b7d](https://github.com/LmeSzinc/StarRailCopilot/commit/27893b7d5c32e40353fbf7e5de95257959db348a) | 通21／已适配 | 新增直接生成二值颜色掩码的工具 |
| 132 | 2026-09-12 | [1d75650d](https://github.com/LmeSzinc/StarRailCopilot/commit/1d75650d4f11338df9ea8b6be417f5db9de56e34) | 通22／已适配 | 颜色计数阈值由相似度改为色差容忍度 |
| 133 | 2026-09-12 | [f0fb197c](https://github.com/LmeSzinc/StarRailCopilot/commit/f0fb197c168bb23bdd289505d78d2229860ec0e4) | 通23／已适配 | 颜色按钮定位参数改名，并改用色差容忍度 |
| 134 | 2026-09-12 | [9de07298](https://github.com/LmeSzinc/StarRailCopilot/commit/9de07298b1054a4842c21ca68d76a8eaa2f9919d) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |
| 135 | 2026-09-14 | [63c10298](https://github.com/LmeSzinc/StarRailCopilot/commit/63c102986d334746f5d6604280996d800017acf7) | 星铁专用／不合入 | 雳涌之径关卡名称纠错 |
| 136 | 2026-09-21 | [bde53a5b](https://github.com/LmeSzinc/StarRailCopilot/commit/bde53a5bfc68d2bccd63fe93a70a0c409ee7d514) | 星铁专用／不合入 | 4.6 兑换码与到期时间 |
| 137 | 2026-09-21 | [b8e192c3](https://github.com/LmeSzinc/StarRailCopilot/commit/b8e192c392ddaddf292c5f3583fcd90b8dc765e5) | 合并／无额外改动 | 合并已有分支提交；合并差异为空，按原始提交分别登记 |

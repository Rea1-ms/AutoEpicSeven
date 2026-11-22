# 开发工具

这里按用途保存可复用的开发工具。新增脚本先找对应分类，临时采样、验证记录和一次性处理脚本放在忽略目录，不放到这一层。

| 目录 | 用途 |
| --- | --- |
| `capture/`（设备截图） | 快捷截图、预览和设备操控。 |
| `assets/`（素材工具） | 切图、框选、素材包装生成与闲置素材暂存。装备洗练专用制作脚本放在其 `equipment_reroll/`（装备洗练）子目录。 |
| `testing/`（离线测试工具） | 统一运行入口、受控失败检查和样本预览。 |
| `exchange_code/`（兑换码工具） | 国服与国际服兑换码资料更新。 |

先在仓库根目录执行 `uv sync --locked`（按锁文件安装运行和开发依赖），再使用虚拟环境解释器，以模块方式启动。下面命令中的 `python`（解释器）在本机替换为 `.venv/Scripts/python.exe`（虚拟环境解释器）。两种安装方式及锁文件维护见[依赖安装与维护](../docs/dependencies.md)。

| 需要做什么 | 命令 |
| --- | --- |
| 图形窗口截图与操控 | `python -B -m dev_tools.capture.screenshot`（默认使用已有的 aes 配置，可加设备选择参数；不需要全局热键依赖） |
| 实时预览、冻结后框选 | `python -B -m dev_tools.assets.asset_crop live`（与截图入口共用窗口；空格冻结画面） |
| 原有终端快捷截图 | `python -B -m dev_tools.capture.screenshot --console`（回车或全局快捷键截图，需要键盘监听依赖） |
| 只保存一帧 | `python -B -m dev_tools.assets.asset_crop capture --help`（查看设备和采样命名参数） |
| 从已有原图制作素材 | `python -B -m dev_tools.assets.asset_crop make --help`（查看无界面切图参数） |
| 生成指定模块的包装 | `python -B -m dev_tools.assets.button_extract --module <完整模块路径>`（先填写实际模块，不运行无参数全量生成） |
| 暂存闲置素材 | `python -B -m dev_tools.assets.asset_archive --help`（默认预览，核对依赖后再移动） |
| 列出正式离线用例 | `python -B -m dev_tools.testing.run_offline_tests --list`（不连接设备） |
| 执行受控失败检查 | `python -B -m dev_tools.testing.check_offline_negative`（验证断言失败、报告和设备隔离） |

截图由设备连接直接读取并保存到电脑，不依赖模拟器共享目录。快捷截图保存在 `screenshots/dev_screenshots/`（开发截图目录），切图工具的手动采样位置按其实际预检输出核对。素材原图需要完整的 1280×720 游戏画面。

图形窗口可用 `--config <配置名>`（选择已有项目配置）或 `--serial <设备地址>`（直接选择设备）。实时画面里左键点击或拖动即可操控，底部按钮支持截图、冻结、返回、完成框选和退出；`F3`（当前窗口截图键）与 `S`（截图键）保存当前显示的整帧原图，`B`（返回键）返回上一页。保存的图片不包含工具栏、选区标记和预览缩放。

截图工具默认视频帧率上限为每秒 30 帧，可用 `--fps 60`（设置每秒 60 帧上限）调整，参数须为正整数。截图入口、`live`（实时预览）和 `capture`（单帧采样）入口均支持。直接选择设备时使用 scrcpy（设备视频传输）；选择已有配置时沿用其截图方式，此参数仅对视频传输生效。实际帧率取决于设备、传输和解码速度。

截图和操控方式分别用 `--screenshot-method`（截图方式）和 `--control-method`（操控方式）选择，例如 `python -B -m dev_tools.capture.screenshot --screenshot-method scrcpy --control-method maatouch`（视频截图配合触摸操控）。截图支持 `scrcpy`（视频传输）、`adb`（设备截图）、`adbnc`（套接字传输截图）、`uiautomator`（设备自动化服务截图）、`ascreencap`（原生截图服务）、`droidcast`（图像传输服务）、`nemu`（MuMu 本地接口）等；操控支持 `maatouch`（触摸服务）、`minitouch`（触摸服务）、`uiautomator`（设备自动化服务）、`adb`（设备输入）、`scrcpy`（视频服务操控）和 `nemu`（MuMu 本地接口）。参数同样适用于实时预览与单帧采样。未指定的方法沿用已有配置，直接选择设备默认使用视频传输与触摸服务；参数覆盖仅用于本次运行。

MuMu 本地接口仅适用于 Windows（操作系统）上的 MuMu 模拟器，redroid（远程安卓容器）可通过普通设备连接使用视频传输、设备截图或设备自动化服务，配合触摸服务操控。使用 `http://`（服务地址）连接时，现有连接层仅支持设备截图、设备自动化服务或原生截图服务，操控仅支持设备输入、设备自动化服务或 minitouch（触摸服务）。

按空格冻结后，左键拖动改为框选素材，不再点击设备；滚轮缩放、右键平移，`Z`（撤销选区键）撤销最后一项。清空选区后可按空格恢复实时操控，框选完成按回车，随后按原有流程在终端填写素材信息。关闭窗口或按退出按钮结束。截图窗口用于手动开发，同一设备上运行自动任务前应先结束手动操控。

已完成的一次性历史迁移脚本及专属测试源码归档到 `.cache/dev_tools/historical_migration/`（历史迁移缓存），不作为日常维护入口，正式测试不依赖缓存。日常直接维护分类清单并运行一致性检查。专项素材脚本会写入素材，整理目录后不会自动执行。旧生成包装头部可能仍记录整理前的命令，后续使用本页的新入口；不为更新注释手改或全量重建包装。

目录归属与新增工具规则见项目技能的开发工具规范。运行产物保留在忽略目录，由用户手动清理。

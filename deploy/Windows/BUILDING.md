# Windows 发行目录构建

发行目录由四部分组成：当前仓库中已经提交的文件、完整的 64 位便携 CPython 3.10.x、Alasio 的实际前端，以及 Alasio 的桌面端外壳。原版打包结构使用 WinPython，本项目继续沿用这一类完整便携发行版，不使用 uv 管理的 Python 作为发行运行时。

## 1. 构建实际前端

先在 Alasio 仓库的 `frontend` 目录构建实际界面。后端会从
`toolkit/Lib/site-packages/frontend/build` 提供这些文件；缺少时桌面端虽然能够打开，
但进入主界面后只会显示 `Not Found`。

```powershell
pnpm install --frozen-lockfile
pnpm check
pnpm build
```

构建结果应位于 `frontend/build`，并至少包含 `index.html` 和 `_app` 目录。

## 2. 构建桌面端外壳

先在 Alasio 仓库的 `webapp` 目录完成检查和构建。依赖安装与 pnpm 12 的构建脚本许可见该目录下的 `BUILDING.md`。

```powershell
pnpm install --frozen-lockfile
pnpm check
pnpm build
pnpm exec electron-builder --win dir
```

构建结果应位于 `webapp/release/win-unpacked`，并至少包含 `Alasio.exe` 和 `resources/app.asar`。

这里必须先运行 `pnpm build`，由构建后的 CSP 修正步骤更新全部内联脚本哈希，再运行
electron-builder。直接执行 `vite build` 后紧接 electron-builder，会让 SvelteKit 启动脚本被
CSP 拦截，最终表现为桌面端白屏。Alasio 的 `pnpm package` 已按相同顺序执行这两个步骤。

## 3. 组装发行目录

回到 AutoEpicSeven 仓库，在所有受版本控制的改动均已提交后执行：

```powershell
pwsh -File .\deploy\Windows\build_release.ps1 `
  -WebAppPath D:\Files\Python\Alasio\webapp\release\win-unpacked `
  -FrontendPath D:\Files\Python\Alasio\frontend\build `
  -PortablePythonPath D:\Files\Python\AutoEpicSeven-test-pack\toolkit
```

`PortablePythonPath` 必须指向包含 `python.exe`、完整标准库和动态库的 64 位 CPython 3.10.x 目录，且不能包含旧的 `WebApp`。脚本只复制该目录，不会修改作为来源的便携运行时。

复制完成后，脚本通过 `uv export --frozen` 从现有 `uv.lock` 导出依赖，再通过随包的 `uv.exe` 同步发行目录内的第三方包，不会修改锁文件，也不会让 uv 提供 Python 本体。

默认输出为 `build/release/AutoEpicSeven-<提交号>`。同名输出已存在时脚本会停止，不会清理或覆盖旧产物；可通过 `-ReleaseName` 指定新名称。

当前的 `config/deploy.yaml`、`config/aes.db` 和 `config/gui.db` 不会打包。两个部署模板会保留，首次运行可据此生成实际配置。

`FrontendPath` 必须指向 Alasio 已构建的 `frontend/build`。组装脚本会将它复制到
`toolkit/Lib/site-packages/frontend/build`，并把整棵目录的 SHA-256 写入发行清单；
更新脚本会在覆盖程序文件前重新核对。

## 4. 更新并启动现有发行目录

先关闭 AutoEpicSeven 和 Alasio 桌面端，再使用新发行目录更新现有安装目录：

```powershell
pwsh -File .\deploy\Windows\update_release.ps1 `
  -InstallPath D:\Apps\AutoEpicSeven `
  -PackagePath D:\Downloads\AutoEpicSeven-new
```

更新脚本只覆盖程序文件，不清理安装目录中仅由旧版本提供的文件。`config/deploy.yaml`、
`config/aes.db` 和 `config/gui.db` 会被明确排除；更新前后还会核对它们的 SHA-256、
Windows ACL 和文件属性，任一变化都会终止并报错。新发行目录如果意外包含这些用户数据，
脚本会在复制前拒绝执行。

复制前还会读取 `release-manifest.json`，确认目录来自 AutoEpicSeven 的完整发行流程，并核对
便携 Python、uv、锁文件、实际前端、桌面端程序和 `app.asar` 的 SHA-256。文件不完整或内容与
清单不符时不会开始更新。

更新完成后只从桌面端入口启动：

```powershell
.\toolkit\WebApp\Alasio.exe
```

不要直接运行 `toolkit/python.exe gui.py`。后者只启动后端，不会建立桌面端认证和窗口生命周期；
重复启动还可能占用配置中的端口，导致桌面端后端启动失败。

## 5. 当前验证记录

2026-09-13 已完成一次完整发行回归：

- `build_release.ps1` 自动装入并校验实际前端、桌面端和便携 CPython；不再需要手动复制前端。
- `update_release.ps1` 更新测试安装目录后，原有 `deploy.yaml`、`aes.db` 和 `gui.db` 的内容、
  Windows ACL 与文件属性均保持不变。
- 桌面端能够读取原端口、密码和任务配置；圣域日常任务已在模拟器中完整运行成功。
- Alasio 桌面端已修正顶部拖拽区域：语言、主题和左侧配置卡可以点击，中部标题空白区仍可拖动窗口。

Alasio 的 `pnpm package` 会先执行 CSP 修正，再运行 electron-builder；不要拆开或调换这两个步骤。
后续仍需按任务分批进行实机回归，当前结果不代表所有任务都已验证。

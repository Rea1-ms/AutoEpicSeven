# Windows 发行目录构建

发行目录由三部分组成：当前仓库中已经提交的文件、完整的 64 位便携 CPython 3.10.x，以及 Alasio 已构建的桌面端目录。原版打包结构使用 WinPython，本项目继续沿用这一类完整便携发行版，不使用 uv 管理的 Python 作为发行运行时。

## 1. 构建桌面端

先在 Alasio 仓库的 `webapp` 目录完成检查和构建。依赖安装与 pnpm 12 的构建脚本许可见该目录下的 `BUILDING.md`。

```powershell
pnpm install --frozen-lockfile
pnpm check
pnpm exec vite build
pnpm exec electron-builder --win dir
```

构建结果应位于 `webapp/release/win-unpacked`，并至少包含 `Alasio.exe` 和 `resources/app.asar`。

## 2. 组装发行目录

回到 AutoEpicSeven 仓库，在所有受版本控制的改动均已提交后执行：

```powershell
pwsh -File .\deploy\Windows\build_release.ps1 `
  -WebAppPath D:\Files\Python\Alasio\webapp\release\win-unpacked `
  -PortablePythonPath D:\Files\Python\AutoEpicSeven-test-pack\toolkit
```

`PortablePythonPath` 必须指向包含 `python.exe`、完整标准库和动态库的 64 位 CPython 3.10.x 目录，且不能包含旧的 `WebApp`。脚本只复制该目录，不会修改作为来源的便携运行时。

复制完成后，脚本通过 `uv export --frozen` 从现有 `uv.lock` 导出依赖，再通过随包的 `uv.exe` 同步发行目录内的第三方包，不会修改锁文件，也不会让 uv 提供 Python 本体。

默认输出为 `build/release/AutoEpicSeven-<提交号>`。同名输出已存在时脚本会停止，不会清理或覆盖旧产物；可通过 `-ReleaseName` 指定新名称。

当前的 `config/deploy.yaml`、`config/aes.db` 和 `config/gui.db` 不会打包。两个部署模板会保留，首次运行可据此生成实际配置。

## 3. 更新现有发行目录

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
便携 Python、uv、锁文件、桌面端程序和 `app.asar` 的 SHA-256。文件不完整或内容与清单不符时
不会开始更新。

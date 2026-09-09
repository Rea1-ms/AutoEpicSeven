# Windows 发行目录构建

发行目录由三部分组成：当前仓库中已经提交的文件、`uv` 管理的 64 位 CPython 3.10.19，以及 Alasio 已构建的桌面端目录。

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
  -WebAppPath D:\Files\Python\Alasio\webapp\release\win-unpacked
```

脚本通过 `uv export --frozen` 从现有 `uv.lock` 导出依赖，再通过 `uv pip sync` 安装到发行目录的 Python 运行时，不会修改锁文件。

默认输出为 `build/release/AutoEpicSeven-<提交号>`。同名输出已存在时脚本会停止，不会清理或覆盖旧产物；可通过 `-ReleaseName` 指定新名称。

当前的 `config/deploy.yaml`、`config/aes.db` 和 `config/gui.db` 不会打包。两个部署模板会保留，首次运行可据此生成实际配置。

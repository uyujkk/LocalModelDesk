# Third-party notices / 第三方组件说明

Local Model Desk application source is MIT-licensed. The Windows portable distribution also uses the separately licensed components below. The license texts provided with that distribution are in `third-party-licenses/`.

Local Model Desk 应用源码采用 MIT 许可证。Windows 便携包还包含下列独立授权的组件，随包许可证正文位于 `third-party-licenses/`。

| Component | Version / provenance | Notices |
|---|---|---|
| Strata Overview renderer | Adapted from [Niko1221/Strata](https://github.com/Niko1221/Strata) | [Original MIT notice](source/strata_overview/LICENSE.txt) |
| Qt, PySide6 Essentials and Shiboken6 | 6.12.0, unmodified binaries from the official Python wheels | LGPL-3.0 with the included GPL-3.0 and LGPL-3.0 texts. The Qt source archives contain the individual module and bundled component notices. |
| CPython Windows runtime | 3.12.15 | Python Software Foundation license and bundled runtime notices |
| PyInstaller bootloader | 6.22.3 | GPL with PyInstaller's bootloader exception |
| psutil, Rich, Pygments, markdown-it-py and mdurl | Versions recorded with their included license files | See their included notices |

For equivalent access to the Qt and PySide source used in the Windows package, download `LocalModelDesk-Qt-6.12.0-corresponding-source.zip` from the [v0.9.1 Release](https://github.com/uyujkk/LocalModelDesk/releases/tag/v0.9.1). It contains the official PySide/Shiboken, Qt Base, Qt SVG, Qt Image Formats and Qt Translations source archives. The Windows package keeps the Qt libraries and Python extension modules as separate files under `_internal`. You can replace them with ABI-compatible builds, or build the application from this repository against your own compatible Qt/PySide runtime. The application's license does not restrict reverse engineering for debugging modifications to those LGPL components.

Windows 包所用的 Qt 和 PySide 对应源码位于同一 [v0.9.1 发布页](https://github.com/uyujkk/LocalModelDesk/releases/tag/v0.9.1) 的 `LocalModelDesk-Qt-6.12.0-corresponding-source.zip`。其中包含 PySide/Shiboken、Qt Base、Qt SVG、Qt Image Formats 和 Qt Translations 的官方源码压缩档。Qt 动态库和 Python 扩展模块独立保存在 `_internal` 下，可以替换为 ABI 兼容版本，也可以用自己的兼容 Qt/PySide 环境构建应用。应用许可不限制为调试这些 LGPL 组件修改而进行的逆向工程。

# 第三方组件

MCLite 应用源码使用 Python 标准库。Windows 单文件 EXE 由 PyInstaller 打包，随包包含 Python 运行时、标准库、Tcl/Tk 与必要的加载组件。

| 组件 | 上游 | 许可文件 |
|---|---|---|
| CPython 3.14.2 与标准库 | https://www.python.org/ | `licenses/PYTHON-LICENSE.txt`，包含 Python 及其捆绑组件声明 |
| Tcl/Tk 8.6 | https://www.tcl.tk/ | `licenses/*-license.terms` |
| PyInstaller 6.22.3 | https://pyinstaller.org/ | `licenses/pyinstaller-COPYING.txt`；GPL 附有允许分发构建产物的引导程序例外 |

`licenses/` 同时保留构建工具可取得的许可文件（例如 altgraph、pefile、pywin32-ctypes）；列出不表示所有开发工具都作为完整软件捆绑进入 EXE。

第三方许可不受 MCLite 的 CC BY-NC-SA 4.0 应用许可替换。Minecraft、Java、加载器、Mod 和整合包在用户选择相关功能后从各自来源下载，保留原有权利和许可，不包含在发布资产中。

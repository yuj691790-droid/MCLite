# MCLite

**Release Preview 0.0.1** · Windows x64 · Python + Tk

轻量的 Minecraft: Java Edition 启动器，应用源码仅使用 Python 标准库。

**预览版限制：正版登录尚不能完成。** Microsoft OAuth → Xbox Live → XSTS 已通过真实账号验证，但 Minecraft Services 拒绝当前应用注册，返回 `Invalid app registration`。尚未成功进入游戏主菜单。

[下载预览版](https://github.com/yuj691790-droid/MCLite/releases/tag/v0.0.1-preview) · [发布说明](docs/RELEASE-0.0.1-preview.md) · [维护说明](docs/MAINTENANCE.md)

**作者的话**

本项目实属PCL启动器下载功能被永久停用的无奈产物，一切知识产权归PCL原作者龙腾猫跃所有，本项目仅是对PCL的拙劣模仿。

<sub>希望别被那几个搞私服的拿下。</sub>

<details>
<summary>一段小字</summary>

<sub>Attacking other creators' work without hesitation for personal gain and harming their legal rights will surely be met with the harshest condemnation and sanctions.</sub>

</details>

## 功能

- 原版安装：客户端、平台库、原生库解压、资源与日志配置，支持散列校验、断点续传和 BMCLAPI 镜像。
- Java 探测与安装：支持安装 Temurin 17 / 21；按游戏元数据选择 Java。
- Microsoft 设备码登录、令牌刷新、多账号保存（目前在 Minecraft 应用准入阶段被阻断）。
- Fabric、Forge、NeoForge 安装。
- Modrinth 搜索、下载和必需依赖；CurseForge 搜索、下载（需要 API Key）。
- Modrinth `.mrpack`、CurseForge `.zip` 整合包导入及实例扫描。
- Tk GUI 与命令行入口。

上述列表说明实现范围；本次实测边界见下文。首个预览版不含自动更新、OptiFine、LiteLoader 或第三方登录。

## 运行

Windows x64 用户下载 Release 中的便携包，解压后运行 `MCLite.exe`，无需预装 Python。

源码运行需要 Python 3.10+ 和 Tk：

```powershell
python run.pyw
```

设置、账号和默认游戏目录位于 `%APPDATA%\MCLite`：

```text
settings.json      设置
accounts.json      登录令牌（明文 JSON，不应公开分享）
game/              游戏与资源
java/              下载的 Java
logs/              启动器和游戏日志
```

Java、游戏文件和账号数据均不包含在发布包内。

## Microsoft 登录

点击“登录 Microsoft”后，在自己的浏览器打开显示的网址并输入设备码；可以在另一台设备完成授权。MCLite 不接收账号密码。

预览版默认使用 MCLite 自有公共客户端 ID。Microsoft、Xbox Live、XSTS 已实测通过，但 Minecraft Services 尚不接受该应用注册，当前版本不能完成正版登录。

自定义 Client ID 可在设置页填写。应用须支持个人 Microsoft 账户并开启公共客户端流；设备码流程不需要 Client Secret 或重定向 URI。注册 Azure 应用不等于获得 Minecraft API 准入，见 <https://aka.ms/AppRegInfo>。

## 命令行

```powershell
python -m mclite doctor
python -m mclite list
python -m mclite install 1.20.1
python -m mclite install 1.20.1 --loader fabric
python -m mclite java --major 17
python -m mclite launch 1.20.1 --offline Steve --dry-run
```

`--dry-run` 仅生成参数，不代表游戏已经运行。CurseForge 需要在设置里提供 API Key；部分作者不允许第三方下载。

## 开发与打包

```powershell
python -m unittest discover -s tests -v
python -m pyflakes mclite tests tools run.pyw
build_windows.bat
```

运行依赖仅标准库；开发检查需安装 `pyflakes`，构建依赖见 `requirements-build.txt`。EXE 输出在 `dist/MCLite.exe`。仓库带 Windows CI，运行静态检查、测试、自检并打包。

## 实测情况

- Windows / Python 3.14.2：真实 Tk 界面、版本清单、Modrinth 搜索正常。
- Java 17 安装并探测成功。
- 原版 1.20.1 客户端、43 个 classpath 库、7 个原生库包、3598 项资源散列校验通过，解压 7 个 DLL。
- 78 项单元测试通过；pyflakes 无告警。
- Microsoft OAuth、Xbox Live、XSTS 通过；Minecraft Services 拒绝应用准入，尚未验证进入游戏主菜单。
- 其他加载器、CurseForge、整合包等主要通过本地夹具测试，未完成全部 Windows 实际账号/安装验证。

## 许可

- 源代码：**MIT**，见 [LICENSE](LICENSE)。
- 官方应用发行物的项目自有部分：**CC BY-NC-SA 4.0**，见 [应用许可](LICENSE-APPLICATION.md)。该许可不撤销或限制 MIT 源码的使用、重新编译和分发权利。
- 第三方组件保持各自许可，见 [第三方声明](THIRD-PARTY-NOTICES.md)。

实现参考 [MC-Mini](https://github.com/yuj691790-droid/MC-Mini) 的思路与公开接口，未复制其代码。项目不是 Mojang 或 Microsoft 官方产品，不包含 PCL、Minecraft 游戏文件或 Java 运行时。

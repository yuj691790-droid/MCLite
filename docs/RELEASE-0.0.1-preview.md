# Release Preview 0.0.1

MCLite 首个公开预览版，适用于 Windows x64。提供单文件 EXE、便携发布包与 MIT 源码。

## 验证结果

- Windows / Python 3.14.2 上完成真实 Tk 界面和 Modrinth 搜索验证。
- 原版 Minecraft 1.20.1 安装完成：客户端 SHA-1 通过；43 个 classpath 库、7 个原生库包、3598 项资源校验无错误，解压 7 个 DLL。
- Java 17 安装并探测成功；版本要求优先读取游戏元数据。
- Microsoft OAuth、Xbox Live、XSTS 实际账号验证通过。
- 修复旧版库主 JAR 漏装、native 路径越界、日志配置失效、GUI Java 选择、Tk 后台读取控件、游戏目录保存后列表未刷新和 Windows 测试隔离问题。

## 已知限制

**正版登录目前无法完成，也未验证进入游戏主菜单。** MCLite 自有应用 Client ID 在 Minecraft Services 阶段被拒绝，返回 `Invalid app registration`，需要应用准入。预览版保留登录实现与可配置 Client ID，不承诺开箱即用的正版登录。

首次 GUI 安装遇到网络 TLS EOF，使用同一安装器重新执行后补齐剩余资源并完成校验；尚未加入新的网络回退机制。

Forge、NeoForge、CurseForge、整合包和多版本启动主要由本地夹具覆盖，未在本次 Windows 验证中完成所有真实安装。应用未签名。

## 许可

- 程序源代码：MIT，见根目录 `LICENSE`。
- 官方应用发行物中项目自有部分：CC BY-NC-SA 4.0，见 `LICENSE-APPLICATION.md`。
- 第三方组件保持原许可证。MIT 源码权利不受应用发行许可撤销或限制。

## 运行

解压 Windows x64 发布包并运行 `MCLite.exe`；不需要预装 Python。Java 与游戏资源按需下载安装，不包含在发布包内。

设置、账号令牌与游戏默认存放于 `%APPDATA%\MCLite`；账号令牌为明文 JSON，不应提交到仓库或随包分发。

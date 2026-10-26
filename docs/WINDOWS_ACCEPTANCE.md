# Windows 验收清单

按顺序做，每步都有“预期结果”。任何一步失败，把对应的日志发我。

## 0. 环境

```bat
python --version
```

预期：3.10 及以上。没有 Python 就先装（安装时勾选 Add python.exe to PATH）。

## 1. 一键自检

```bat
check_windows.bat
```

它会跑 `doctor` 和全部单元测试。预期：

- 游戏目录可写
- 检测到 Java（或提示没有，可用 `python -m mclite java --major 21` 安装）
- 版本清单可达
- 72+ 项测试 OK

## 2. 界面

```bat
python run.pyw
```

预期：出现 MCLite 窗口，四个页签（启动 / 下载 / Mod / 设置），底部状态“就绪”。
失败：弹窗会给出日志路径，或直接看 `%APPDATA%\MCLite\logs\mclite.log`。

## 3. 下载原版

下载页选 `1.20.1` → 加载器“原版” → “下载并安装”。

预期：进度条推进，结束后启动页出现 `1.20.1`。
首次约 650 MB；网络差时靠断点续传，中断后重新点安装即可继续。

## 4. 启动（先离线，排除登录因素）

```bat
python -m mclite launch 1.20.1 --offline Steve --wait
```

预期：Minecraft 窗口出现，关闭后命令行打印 `exit=0`。
日志：`%APPDATA%\MCLite\logs\game-1.20.1-*.log`。

## 5. 微软登录

界面右上角“登录 Microsoft”。

预期：浏览器打开 microsoft.com/link，窗口显示 8 位代码；输入并授权后，界面显示玩家名。

若报 `invalid_client` / `unauthorized_client`：按 README 注册自己的 Azure 应用并填入客户端 ID。

## 6. 加载器

分别安装 Fabric、Forge、NeoForge 各一个，启动一次。

预期：启动页出现对应的加载器版本；启动后主菜单显示对应加载器（Forge/NeoForge 会在日志里出现 `BootstrapLauncher`）。

## 7. Mod 与整合包

- Mod 页搜 `sodium`，下载到 `mods/`（Fabric）
- 导入一个 `.mrpack`，确认版本列表出现 `包名 / 版本` 并能启动
- 有 CurseForge Key 时，试一次搜索和 `.zip` 导入

## 需要反馈的内容

| 失败环节 | 发我什么 |
| --- | --- |
| 界面起不来 | `%APPDATA%\MCLite\logs\mclite.log` |
| 下载失败 | 界面报错文字 + 对应 URL |
| 启动失败 | `logs/game-*.log` 最后 50 行 |
| 登录失败 | 弹窗文字（含 AADSTS 代码） |

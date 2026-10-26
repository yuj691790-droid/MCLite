# 维护说明

## 调用关系

- `run.pyw` 安装异常日志钩子并启动 `ui/app.py`；`python -m mclite` 进入 `cli.py`。
- GUI 和 CLI 复用 `meta.py`、`installer.py`、`launch.py`，功能修复应优先落在这些公共模块。
- `Version.load` 读取本地或远端 JSON，合并加载器继承；`Version.jar_id` 指向实际客户端 JAR。
- `Installer` 安装客户端、库、资源和日志配置，再解压 native；`build_command` 组装参数，`launch` 校验文件并创建子进程。

## 容易破坏的约定

- Tk 控件只能在主线程访问。`_async.work` 不读写控件；UI 输入先在调用线程读取，结果经 `events` 交给 `_pump`，再执行 `done/failed`。
- `java_combo` 索引 0 表示自动匹配，1 开始对应 `javas[index - 1]`。以版本 JSON 的 `javaVersion.majorVersion` 为准，旧版缺少该字段才按版本号推断；用户手选的 Java 优先。
- 旧版库条目可以同时包含 `downloads.artifact` 和 `downloads.classifiers`：前者加入 classpath，后者解压。现代 `natives-*` 条目只用于 native。
- 日志配置文件名使用 `logging.client.file.id` 原值，不能再加 `.xml`；`logging.client.argument` 是 JVM 参数，必须位于主类前面。
- 外部压缩包路径必须用解析后的目录关系校验。字符串 `startswith` 不是路径边界检查。
- 下载完整性由 `DownloadTask` 的散列/大小校验和 `.part` 替换机制维护，不能通过忽略校验失败来“修复”网络故障。
- UI 测试必须隔离 `ConfigStore` 和 Tk 替身；日志测试必须在清理临时目录前关闭文件句柄，避免 Windows 文件占用错误。

## 验证

在项目目录执行：

```powershell
python -m unittest discover -s tests -v
python -m pyflakes mclite tests tools run.pyw
python -m mclite doctor
python run.pyw
```

运行只需要标准库；`pyflakes` 是开发检查工具。单元测试的网络、账号和下载多用本地夹具，不代表真实登录、完整游戏下载或 Forge 安装已经通过。

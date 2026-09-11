# 导出位置选择（2026-09-07）

- 单张素材包、批量转换开始导出前，弹出保存位置窗口。
- 桌面版点击“选择文件夹…”使用系统文件夹选择器；也可直接填写完整路径。
- 支持中文、空格及新建目录；成功导出后记住目录，重启仍保留。
- 每次结果保存到独立子文件夹；取消窗口不会开始导出。无效路径或不可写目录会提示。
- 复用已有 pywebview FileDialog.FOLDER、Python pathlib/tempfile 和现有导出器，无新依赖。本项目维护路径校验、位置记忆和前端交互。

验证：52 项 Python 测试、4 项 Node 测试、JavaScript 语法检查和 PyInstaller 构建通过。实际启动打包后的服务，验证中文路径 PNG/报告导出及重启后位置记忆。

验证边界：系统文件夹对话框通过模拟窗口测试，尚未人工点击验证原生 WebView 窗口。浏览器模式需填写完整路径。

程序：dist/export-location-20260907/LayerRebirth/LayerRebirth.exe。请保留同目录的 _internal 文件夹。

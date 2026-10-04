# 图层重生 Layer Rebirth

面向自媒体和电商用户的 Windows 本地图片可编辑化工具。它不是简单修改后缀，而是把 PNG/JPG/WebP/BMP 恢复成可检查、可修改、可交付的素材工程。

## 当前首版能力

- 营销图可编辑化：本地 OCR 提取中英文文字，背景修补，低置信度文字明确预警。
- Logo 精细矢量化：VTracer 真矢量路径、颜色精度、杂点过滤和节点数量体检。
- 黑白 Logo 拼版会自动启用高精度二值描摹，保留小字和细线；强制选择营销图时启用保真保护，不再用通用字体破坏性覆盖原稿。
- 批量格式转换：一次处理最多 30 张图片，统一尺寸、DPI、留白背景和 PNG/JPG/WebP/PDF 输出。
- 轻量修正：可直接点击图片中的文字原位修改，也可在右侧编辑文字、字体、颜色和透明度；支持隐藏、删除和排序图层。
- 密集拼版中的候选文字无需勾选即可定位；修改时自动清除对应原字，并可一键恢复原稿。
- 商用素材包：`editable.svg`、`outlined.svg`、`print.pdf`、`legacy.eps`、预览图、质量报告和工程文件。
- 全程绑定 `127.0.0.1`，核心处理不需要网络，也不会上传图片。
- 默认开放全部功能，无需许可证；支持完整素材包、批量转换和无水印导出。
- 去除水印／杂物：独立任务，支持涂抹清除、框选清除和保护画笔；本地修补，可比较原图、撤销修补并导出。原图保留，保护区域与选区外像素不变。纯色背景恢复周围颜色，纹理背景使用 OpenCV；复杂纹理、人物和大面积遮挡需要人工检查，无法恢复被遮挡的真实细节。

## 文字编辑增强版（2026-09-07）

选中文字后，在右侧“原字背景处理”中选择：

- 自动修补原字：沿用原有修补，复杂背景需要检查。
- 透明叠字（不覆盖底图）：关闭原字覆盖补丁，避免白色块遮住图片；原字本身也会保留，可移动新文字避免重叠。
- 自选颜色覆盖原字：指定覆盖底色。

文字工具包括本机可用字体、字号、粗体、斜体、下划线、删除线、字距、行距、多行、左／中／右对齐、旋转、描边颜色与宽度、文字颜色／透明度，以及位置／文本框尺寸。可选择等比缩小或使用指定字号。
右侧输入框 Enter 分行；画布内 Shift+Enter 分行、Enter 保存。文字移动时，清除原字的区域仍固定在原位置。
所有设置随工程保存，支持撤销重做；预览及 SVG/PDF/EPS/PNG 共用文字布局。本机字体列表不代表每种字体均支持中文；缺字时请换用支持该文字的字体。SVG 在另一台机器上仍依赖其字体，转曲 SVG 更适合保留字形。

## 本地运行

需要 Windows 10/11 64 位、Python 3.12（开发验证版本）及 WebView2 Runtime。首次安装依赖需要联网；安装完成后核心图片处理在本机运行。

```powershell
git clone https://github.com/g752152605-ops/layer-rebirth.git
cd layer-rebirth
setup_windows.cmd
start_layer_rebirth.cmd
```

### 稳定性优化版

重任务独立进程运行；处理中显示已用时间，可点击“取消任务”。后台任务超过 180 秒自动终止并恢复到可重试状态。复杂图片可缩小或分批处理。
编辑预览最长边为 1600px，缓存不变的底图；最终导出仍保留原始尺寸。详情及实测见 [STABILITY_RELEASE.md](STABILITY_RELEASE.md)。

### 2026-09-07 优化版

- 普通营销图默认保留原始位图，只在用户改字时局部修补；未修改文字不会被替代字体覆盖，可恢复原稿。
- 文案保持字形比例，短文字不拉宽，长文字等比缩小；非常长的文案仍需检查可读性。
- 增加最近工程，可打开当前本机数据目录中的工程继续编辑。
- 增加本次编辑最多 30 步撤销／重做，支持改字、删除、排序和隐藏。应用后自动保存；刷新或重开工程后不保留撤销历史。输入框内保留原生撤销，输入框外可用 Ctrl+Z / Ctrl+Y。
- PNG/JPG/WebP 导出保持设定像素尺寸，DPI 不再改变像素数量；PNG/WebP 支持透明背景。PNG/JPG 写入 DPI 元数据；WebP 不承诺通用 DPI 元数据支持。PDF 按像素尺寸与 DPI 确定物理尺寸。
- 质量评分仅为原图分析和基础检查参考；PDF 未完成 CMYK、出血或印前校验。

实施范围及验收见 [OPTIMIZATION_PLAN.md](OPTIMIZATION_PLAN.md)。沿用现有依赖，无新增框架；本项目维护局部改字编排、工程历史和导出规则。

首次运行双击 `setup_windows.cmd` 安装独立环境，之后双击 `start_layer_rebirth.cmd`。

开发模式：

```powershell
.\.venv\Scripts\python.exe run_app.py --browser --port 8765 --data-dir .\tmp\dev-data
```

## 测试与打包

测试与打包前安装开发依赖；JavaScript 测试另需 Node.js 18 或以上版本。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
node --test tests/test_history.cjs tests/test_live_preview.cjs tests/test_export_directory.cjs tests/test_workbench.cjs
node --check web/app.js
```

仓库不包含用户图片、工程数据、导出文件、私钥和打包产物。依赖本地真实样本的一项测试在样本不存在时会自动跳过。`license_public_key.pem` 是公开验证密钥；历史授权模块保留在源码中，当前默认功能无需许可证。

## 开源许可

本项目原创代码采用 [MIT License](LICENSE)，允许商用、修改和再分发，需保留许可证及版权声明。第三方组件及模型仍适用各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。用户导入的图片、商标和字体不在本项目许可范围内。

双击 `build_windows.cmd` 会先运行测试，再生成 `dist\LayerRebirth\LayerRebirth.exe`。目标环境为 Windows 10/11 64 位，并需要 WebView2 Runtime；多数当前 Windows 系统已预装。

## 明确限制

- 照片和复杂纹理会保留为位图背景，不宣传为完全矢量化。
- 首版不写入原生 `.ai`、PSD 或 CDR；SVG/EPS 可由 Illustrator 打开并另存为 AI。
- 背景修补最适合纯色和简单渐变。质量体检会标出复杂图建议人工检查。
- 包含多种品牌字体的拼版可以高保真转为路径，但无法自动恢复成对应的原字体文件；若要逐字改动，建议先裁切成单个 Logo 再处理。
- 已完成当前开发机上的打包后进程验证；正式发售前仍需在干净的 Windows 10/11 实机各验证一次。

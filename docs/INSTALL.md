# 安装与运行

完整桌面流程的验证目标是 Windows 10/11、Python 3.12。无需开发助手运行时、Node.js或前端构建；Node.js仅用于部分开发测试。

## 推荐安装

安装Python 3.12（含`py`启动器），下载或克隆本仓库，进入项目目录。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe run.py
```

打开 http://127.0.0.1:8765 。保持终端运行，`Ctrl+C`停止，不会删除业务数据。

也可以运行 `Setup.ps1` 自动创建项目 `.venv` 并安装依赖，然后使用 `Start.ps1` 或双击“启动招投标.cmd”。`Start.ps1`在后台启动本机服务，双击“停止招投标.cmd”可停止它。

如果系统策略阻止PowerShell脚本，优先使用上面的Python命令启动；无需永久降低机器执行策略。不要通过删除自己的虚拟环境或业务目录来排查端口占用。

## 可选 OCR

对扫描PDF和图片识别需另装OCR依赖，再在“模型与企业设置”中启用OCR：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
```

或 `Setup.ps1 -OCR`。OCR可能误识别数字、表格和层级，需人工对照原件；能提取文字不代表版式或内容已准确理解。

## Microsoft Word 与导出

完整投标书的目录域刷新、真实页码定位和同源PDF转换使用本机桌面版Microsoft Word COM。请在当前Windows用户下完成Word安装、激活和首次启动。

部分基础DOCX写出不需Word，但不能代替整个投标书的真实分页能力。当前未提供LibreOffice替代实现。普通测试中的模拟分页不证明本机已安装Word；发布验证会分别说明原生验证结果。

Word提示交互、文件被其他程序占用或COM不可用时，导出会报错，不能将不完整结果当作正式交付文件。

## 设置与数据位置

默认 `data/` 是本机工作数据，包括 `bidding.sqlite3`、原件副本、临时处理文件、检查点和导出文件。密钥、业务数据和日志不属于源码，也不会随Git克隆带到另一台机器。

| 设置 | 用途 |
| --- | --- |
| `MX_DATA_DIR` | 指定独立数据目录；未设置时使用项目的`data/`。 |
| `BIDDING_PORT` | 本机端口，默认8765。直接运行`run.py`和启动脚本均支持。 |
| `DEEPSEEK_API_KEY` | 可选环境变量方式配置密钥。不要提交到仓库。 |
| `LANGFUSE_ENABLED` | 可选遥测，默认关闭；启用前核对接收端及资料外发范围。 |

例如，在另一个新终端启动独立的演示目录：

```powershell
$env:MX_DATA_DIR = (Resolve-Path .\demo-data).Path
$env:BIDDING_PORT = "8766"
.\.venv\Scripts\python.exe run.py
```

快捷脚本也可指定端口：`Start.ps1 -Port 8766 -NoBrowser` / `Stop.ps1 -Port 8766`。相同数据库只能由一个服务实例占用，两个端口不意味着能够并行写同一数据目录。

首次安装公司名、材料路径和模型密钥均为空。进入“模型与企业设置”自行填写，连接检查后选择账户实际可用的模型。不要把别人的加密密钥记录拷贝到自己的机器。

## 安全备份

等待当前任务结束，停止对应服务，然后备份整个数据目录。备份可能含合同、报价、原件、模型上下文和加密密钥，应按业务资料管理，不要上传到公开GitHub。

恢复前保护现有数据，优先在新的独立目录核对备份，不能用旧数据库直接覆盖后来的编辑。Windows DPAPI密钥与用户环境相关，换机或换用户后通常需要重新配置。

## 开发测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

前端测试需要PATH中有Node.js；未安装会明确跳过相应用例。普通测试会隔离数据目录、清除继承的模型密钥并阻止外部网络，模型和分页使用替身。请勿为了测试填写真实凭据。GitHub CI使用Windows、Python3.12与Node.js22。

具体已验证项与限制见[发布验证记录](RELEASE.md)，安全边界见[SECURITY.md](../SECURITY.md)。

# 第三方许可说明

本仓库的MIT许可只覆盖维护者有权许可的原创源码、文档和合成示例，不取代第三方组件的许可证。本仓库不附带vendor、Python解释器、Microsoft Word、OCR模型权重或第三方安装包。

运行依赖通过requirements文件安装，实际版本与直接依赖清单见安装文件。保留上游包随附的许可及版权声明。

## PDF组件

PyMuPDF/MuPDF使用AGPL或上游商业许可。MIT授权不免除组合分发、修改或网络服务涉及的上游义务；若无法满足AGPL要求，应先向上游取得适用的商业许可。不要将本项目理解为所有组合部署均可无条件闭源使用。

上游说明：[PyMuPDF许可](https://pymupdf.io/pymupdf)、[PyMuPDF源码](https://github.com/pymupdf/PyMuPDF)。

## 其他组件与服务

FastAPI、Uvicorn、HTTPX、python-docx、pypdf、Pillow等组件各自保留上游许可证；以安装版本附带文本为准。可选OCR依赖和模型也需分别核对其发布条款。

Microsoft Word是用户自行安装并授权的独立软件，不包含在本仓库。DeepSeek是用户自行选择和授权的外部模型服务，其费用、隐私和服务条款不由MIT覆盖。

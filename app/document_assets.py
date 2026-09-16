"""Bounded, local-only raster extraction with source provenance.

This module never decides whether a source is approved or appropriate for a bid.
Callers must select assets using the SAME approved source/version/scope rules as
text evidence. In particular, a picture is not evidence of completed signatures,
attachments or delivery. Descriptions here are untrusted source text.

No database, model, OCR, network or production-directory defaults are used.
Every output is a decoded PNG; the source remains untouched. DOCX/PPTX locations
are structural, while PDF page numbers refer to actual source pages.
"""
from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import warnings
import zipfile
from html import unescape
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_ZIP_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 10000
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_DIMENSION = 24000
MAX_ASSETS = 1500
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_PDF_PAGES = 3000
VERSION = "document-assets-1"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class _ExtractionLimit(ValueError):
    """Stop the complete scan rather than retrying every remaining image."""


def _image_attempt(result):
    result["_attempts"] = result.get("_attempts", 0) + 1
    if result["_attempts"] > MAX_ASSETS:
        raise _ExtractionLimit("图片引用数量超过上限；剩余图片未扫描")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path, limit=MAX_SOURCE_BYTES) -> str:
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("文件不存在、不是普通文件或超过大小上限")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            if size > limit:
                raise ValueError("读取时文件超过大小上限")
            digest.update(block)
    return digest.hexdigest()


def _warning(result: dict, code: str, message: str, **source) -> None:
    item = {"code": code, "message": message, "source": source}
    if item not in result["warnings"]:
        result["warnings"].append(item)
    result["incomplete"] = True


def _text(node: ET.Element, namespace: str) -> str:
    return "".join(n.text or "" for n in node.iter(namespace + "t")).strip()


def _xml(data: bytes) -> ET.Element:
    if len(data) > MAX_XML_BYTES:
        raise ValueError("XML超过大小上限")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML包含禁止的DTD或实体定义")
    return ET.fromstring(data)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _local_path(path) -> Path:
    text = str(path)
    # UNC paths can cause network I/O despite being accepted by pathlib.
    if text.startswith(("\\\\", "//")):
        raise ValueError("仅支持本地文件，不读取UNC网络路径")
    return Path(path).expanduser().resolve()


def _png(data: bytes):
    from PIL import Image, ImageOps
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图像文件超过大小上限")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in MIMES:
                raise ValueError("仅接受实际可解码的PNG/JPEG/WebP栅格图像")
            width, height = image.size
            if (not width or not height or width * height > MAX_IMAGE_PIXELS
                    or max(width, height) > MAX_IMAGE_DIMENSION):
                raise ValueError("图像像素或尺寸超过上限")
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("多帧图像未提取，避免静默丢失动画帧")
            original_mime = MIMES[image.format]
            image.load()  # Reject truncated or corrupt raster data.
            oriented = ImageOps.exif_transpose(image)
            raster = oriented.convert("RGBA" if "A" in oriented.getbands()
                                      or "transparency" in oriented.info else "RGB")
            options = {}
            profile = image.info.get("icc_profile")
            if profile and len(profile) <= 1024 * 1024:
                options["icc_profile"] = profile
            out = io.BytesIO()
            raster.save(out, format="PNG", **options)
            encoded = out.getvalue()
            if len(encoded) > MAX_IMAGE_BYTES:
                raise ValueError("解码后PNG超过大小上限")
            return encoded, raster.width, raster.height, original_mime


def _emit(result: dict, data: bytes, output: Path, source: dict) -> None:
    if len(result["assets"]) >= MAX_ASSETS:
        raise _ExtractionLimit("图片引用数量超过上限")
    png, width, height, original_mime = _png(data)
    if result.get("_output_bytes", 0) + len(png) > MAX_OUTPUT_BYTES:
        raise _ExtractionLimit("图片输出总量超过上限")
    identity = json.dumps({"version": VERSION, "document_id": result["document_id"],
                           "source_sha256": result["source_sha256"],
                           "source": source, "original_sha256": _sha(data)},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    asset_id = "asset-" + _sha(identity.encode("utf-8"))
    target = output / (asset_id + ".png")
    if target.exists():
        # Do not silently replace a changed extraction, symlink or source file.
        if not _within(target.resolve(), output) or _file_sha(target, MAX_IMAGE_BYTES) != _sha(png):
            raise ValueError("目标图片已被修改或指向输出目录之外，未覆盖")
    else:
        with target.open("xb") as stream:
            stream.write(png)
    result["assets"].append({
        "asset_id": asset_id, "document_id": result["document_id"],
        "source_path": result["source_path"], "source_sha256": result["source_sha256"],
        "source": source, "path": str(target), "mime": "image/png",
        "width": width, "height": height, "bytes": len(png), "sha256": _sha(png),
        "original_mime": original_mime, "original_sha256": _sha(data),
    })
    result["_output_bytes"] = result.get("_output_bytes", 0) + len(png)


def _archive_check(archive: zipfile.ZipFile) -> set[str]:
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES or sum(e.file_size for e in entries) > MAX_ZIP_BYTES:
        raise ValueError("Office压缩包条目数量或解压总量超过上限")
    names = set()
    for entry in entries:
        name = entry.filename
        if (entry.orig_filename != name or name.startswith(("/", "\\")) or "\\" in name or ":" in name
                or ".." in PurePosixPath(name).parts or "\x00" in name or name in names):
            raise ValueError("Office压缩包包含非法路径或重复条目")
        if (entry.flag_bits & 1 or entry.file_size > MAX_IMAGE_BYTES
                or (entry.file_size > 1024 * 1024
                    and entry.file_size > max(1, entry.compress_size) * 300)):
            raise ValueError("Office压缩包含加密、过大或异常压缩比条目")
        names.add(name)
    return names


def _rels(archive: zipfile.ZipFile, names: set[str], part: str) -> dict:
    path = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
    if path not in names:
        return {}
    return {node.get("Id"): dict(node.attrib) for node in _xml(archive.read(path))}


def _target(part: str, rel: dict) -> str:
    raw = unquote(rel.get("Target", ""))
    if (not raw or rel.get("TargetMode", "").lower() == "external"
            or urlsplit(raw).scheme or raw.startswith(("/", "\\")) or "\\" in raw):
        raise ValueError("外链或非法Office图片目标未读取")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(part), raw))
    if target == ".." or target.startswith("../"):
        raise ValueError("Office关系目标越出压缩包")
    return target


def _image_refs(node: ET.Element):
    for child, relationship in _image_ref_nodes(node):
        yield relationship


def _image_ref_nodes(node: ET.Element):
    for child in node.iter():
        if child.tag == A + "blip":
            yield child, child.get(R + "embed") or child.get(R + "link")
        elif child.tag.endswith("}imagedata"):
            yield child, child.get(R + "id")


def _description(node: ET.Element) -> str:
    return " / ".join(filter(None, [child.get("descr") or child.get("title", "")
                                    for child in node.iter()
                                    if child.tag.endswith(("}docPr", "}cNvPr"))]))[:1000]


def _display_transform(node: ET.Element) -> dict:
    """Keep author cropping/rotation visible to the downstream evidence selector.

    Extraction deliberately retains the embedded raster, not a silently edited
    crop. Callers should withhold transformed images unless they can reproduce
    the visible source region. Hidden pixels need not be approved source content.
    """
    transform = {}
    crop = node.find(".//" + A + "srcRect")
    if crop is not None and any(value not in ("0", "0.0") for value in crop.attrib.values()):
        transform["crop"] = dict(crop.attrib)
    for xfrm in node.iter(A + "xfrm"):
        values = {key: value for key, value in xfrm.attrib.items()
                  if key in ("rot", "flipH", "flipV") and value not in ("0", "false")}
        if values:
            transform["rotation_flip"] = values
            break
    return transform


def _office_picture(result, archive, names, output, source, rel):
    _image_attempt(result)
    try:
        if not rel or not rel.get("Type", "").endswith("/image"):
            raise ValueError("图片关系缺失或不是image关系")
        target = _target(source["part"], rel)
        if target not in names:
            raise ValueError("引用的图片正文不在压缩包内")
        source["media_part"] = target
        _emit(result, archive.read(target), output, source)
        if source.get("display_transform"):
            _warning(result, "display_transform_unapplied",
                     "原件图片有裁剪、旋转或翻转；本次保留原始栅格，须核对可见范围后再选入正文", **source)
    except _ExtractionLimit:
        raise
    except Exception as exc:
        _warning(result, "image_unavailable", str(exc)[:240], **source)


def _docx(result, archive, names, output):
    if "word/document.xml" not in names:
        raise ValueError("DOCX缺少word/document.xml")
    styles = {}
    if "word/styles.xml" in names:
        for style in _xml(archive.read("word/styles.xml")).iter(W + "style"):
            name = style.find(W + "name")
            if name is not None:
                styles[style.get(W + "styleId")] = name.get(W + "val", "")
    parts = ["word/document.xml"] + sorted(n for n in names if re.fullmatch(
        r"word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml", n))
    for part in parts:
        root = _xml(archive.read(part))
        relations = _rels(archive, names, part)
        # Paragraph indices include paragraphs inside tables. They are structural
        # asset locators, not a claim to match OCR block numbers or Word pages.
        paragraphs = list(root.iter(W + "p"))
        texts = [_text(p, W)[:1000] for p in paragraphs]
        heading = ""
        parents = {child: parent for parent in root.iter() for child in parent}
        for index, paragraph in enumerate(paragraphs):
            ancestor = paragraph
            deleted = False
            while ancestor in parents:
                ancestor = parents[ancestor]
                if ancestor.tag in (W + "del", W + "moveFrom"):
                    deleted = True
                    break
            if deleted:
                continue
            style = paragraph.find("./" + W + "pPr/" + W + "pStyle")
            style_id = style.get(W + "val", "") if style is not None else ""
            if re.search(r"heading|标题|^title$", styles.get(style_id, style_id), re.I):
                heading = texts[index][:300] or heading
            for occurrence, (ref_node, relationship) in enumerate(_image_ref_nodes(paragraph), 1):
                # A nested textbox paragraph is processed itself, not again from
                # its containing paragraph (which would duplicate its pictures).
                ancestor = ref_node
                own_paragraph = None
                picture_container = None
                deleted = False
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if ancestor.tag.endswith("}pic") and picture_container is None:
                        picture_container = ancestor
                    if ancestor.tag in (W + "del", W + "moveFrom"):
                        deleted = True
                    if ancestor.tag == W + "p" and own_paragraph is None:
                        own_paragraph = ancestor
                if deleted or own_paragraph is not paragraph:
                    continue
                before = texts[index - 1] if index else ""
                after = texts[index + 1] if index + 1 < len(texts) else ""
                source = {"format": "docx", "part": part, "relationship": relationship,
                          "paragraph": index + 1, "occurrence": occurrence, "page": None,
                          "heading": heading, "caption": _description(paragraph) or texts[index]
                          or (after if re.match(r"^(?:图\s*\d|Figure\b)", after, re.I) else ""),
                          "nearby_text": "\n".join(filter(None, (before, texts[index], after)))[:1800]}
                if picture_container is not None:
                    source["display_transform"] = _display_transform(picture_container)
                _office_picture(result, archive, names, output, source, relations.get(relationship))


def _pptx(result, archive, names, output):
    part = "ppt/presentation.xml"
    if part not in names:
        raise ValueError("PPTX缺少ppt/presentation.xml")
    relations = _rels(archive, names, part)
    slides = []
    for node in _xml(archive.read(part)).iter(P + "sldId"):
        relationship = node.get(R + "id")
        rel = relations.get(relationship, {})
        if not rel.get("Type", "").endswith("/slide"):
            _warning(result, "slide_unavailable", "幻灯片关系缺失", relationship=relationship)
            slides.append(None)
        else:
            slides.append(_target(part, rel))
    for slide, part in enumerate(slides, 1):
        if part not in names:
            _warning(result, "slide_unavailable", "幻灯片正文未入库", slide=slide, part=part)
            continue
        root = _xml(archive.read(part))
        relations = _rels(archive, names, part)
        heading = ""
        for shape in root.iter(P + "sp"):
            placeholder = shape.find(".//" + P + "ph")
            if placeholder is not None and placeholder.get("type") in ("title", "ctrTitle"):
                heading = _text(shape, A)[:300]
                break
        nearby = "\n".join(_text(n, A) for n in root.iter(P + "sp"))[:1800]
        picture_refs = set()
        for picture, node in enumerate(root.iter(P + "pic"), 1):
            for occurrence, (ref_node, relationship) in enumerate(_image_ref_nodes(node), 1):
                picture_refs.add(ref_node)
                source = {"format": "pptx", "part": part, "relationship": relationship,
                          "slide": slide, "page": None, "picture": picture, "occurrence": occurrence,
                          "heading": heading, "caption": _description(node), "nearby_text": nearby,
                          "display_transform": _display_transform(node)}
                _office_picture(result, archive, names, output, source, relations.get(relationship))
        parents = {child: parent for parent in root.iter() for child in parent}
        for occurrence, (ref_node, relationship) in enumerate(_image_ref_nodes(root), 1):
            if ref_node in picture_refs:
                continue
            node = ref_node
            while node in parents and node.tag not in (P + "sp", P + "bg"):
                node = parents[node]
            source = {"format": "pptx", "part": part, "relationship": relationship,
                      "slide": slide, "page": None, "picture": None, "occurrence": occurrence,
                      "kind": "shape_or_background_fill", "heading": heading,
                      "caption": _description(node), "nearby_text": nearby,
                      "display_transform": _display_transform(node)}
            _office_picture(result, archive, names, output, source, relations.get(relationship))
        if any(True for _ in root.iter(P + "graphicFrame")):
            _warning(result, "non_raster_drawing", "图表、SmartArt和表格未转为产品截图", part=part, slide=slide)


def _pdf(result, path, output):
    import fitz
    with fitz.open(path) as document:
        if document.needs_pass:
            raise ValueError("PDF有密码，未读取图片")
        if len(document) > MAX_PDF_PAGES:
            raise ValueError("PDF页数超过上限")
        for number, page in enumerate(document, 1):
            images = page.get_images(full=True)
            seen = set()
            for record in images:
                xref, mask = record[:2]
                if xref in seen:
                    continue
                seen.add(xref)
                positions = page.get_image_rects(xref)
                if not positions:
                    continue  # An unused image resource is not visible evidence.
                _image_attempt(result)
                try:
                    width, height = record[2:4]
                    if width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_IMAGE_DIMENSION:
                        raise ValueError("PDF图像像素或尺寸超过上限")
                    if mask:
                        # A soft mask is part of the displayed image, not another
                        # evidence asset. Combine it without changing placement.
                        mask_width = int(document.xref_get_key(mask, "Width")[1])
                        mask_height = int(document.xref_get_key(mask, "Height")[1])
                        if (mask_width * mask_height > MAX_IMAGE_PIXELS
                                or max(mask_width, mask_height) > MAX_IMAGE_DIMENSION):
                            raise ValueError("PDF透明蒙版像素或尺寸超过上限")
                        pix = fitz.Pixmap(document, xref)
                        if pix.alpha:
                            pix = fitz.Pixmap(pix, 0)
                        pix = fitz.Pixmap(pix, fitz.Pixmap(document, mask))
                        data = pix.tobytes("png")
                    else:
                        data = document.extract_image(xref)["image"]
                    texts = [str(b[4]).strip() for b in page.get_text("blocks") if b[6] == 0]
                    for occurrence, rectangle in enumerate(positions, 1):
                        if occurrence > 1:
                            _image_attempt(result)
                        source = {"format": "pdf", "part": None, "relationship": None,
                                  "page": number, "xref": xref, "soft_mask_xref": mask or None,
                                  "occurrence": occurrence, "bbox": list(rectangle), "heading": "",
                                  "caption": "", "nearby_text": "\n".join(texts)[:1800]}
                        _emit(result, data, output, source)
                except _ExtractionLimit:
                    raise
                except Exception as exc:
                    _warning(result, "image_unavailable", str(exc)[:240], page=number, xref=xref)
            # Vector artwork and inline images are intentionally not rasterized
            # as an entire page, which could silently combine unrelated content.
            if page.get_drawings():
                _warning(result, "non_raster_drawing", "本页矢量图形未作为独立栅格图片提取", page=number)
            if any(not i.get("xref") for i in page.get_image_info(xrefs=True)):
                _warning(result, "inline_image_unavailable", "本页内联图像缺少独立引用，未提取", page=number)


class _HTMLImages(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            attrs = dict(attrs)
            self.images.append((self.getpos()[0], attrs.get("alt", ""), attrs.get("src", "")))


def _markdown(result, path, output, roots):
    raw = path.read_bytes()
    if len(raw) > MAX_XML_BYTES:
        raise ValueError("Markdown超过大小上限")
    text = raw.decode("utf-8-sig")
    # Ignore fenced and inline code: sample syntax is not a document image.
    visible = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker.group(1)[0]
            elif marker.group(1)[0] == fence:
                fence = None
            visible.append("")
        else:
            visible.append("" if fence else re.sub(r"`+[^`]*`+", "", line))
    references = {}
    for line in visible:
        match = re.match(r'\s*\[([^]]+)\]:\s*(<[^>]+>|\S+)', line)
        if match:
            references[match.group(1).strip().lower()] = match.group(2).strip("<>")
    heading = ""
    inline = re.compile(r'!\[([^]]*)\]\(\s*(<[^>]+>|(?:\\.|[^()\s]|\([^)]*\))+)(?:\s+["\'][^"\']*["\'])?\s*\)')
    selected = []
    for index, line in enumerate(visible, 1):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            heading = match.group(1)[:300]
        for match in inline.finditer(line):
            selected.append((index, match.group(1), match.group(2).strip("<>"), heading))
        for match in re.finditer(r'!\[([^]]*)\]\[([^]]*)\]', line):
            key = (match.group(2) or match.group(1)).strip().lower()
            selected.append((index, match.group(1), references.get(key, ""), heading))
    html = _HTMLImages()
    html.feed("\n".join(visible))
    selected += [(line, alt, ref, "") for line, alt, ref in html.images]
    for occurrence, (line, alt, reference, heading) in enumerate(sorted(selected), 1):
        _image_attempt(result)
        source = {"format": "markdown", "part": None, "relationship": None,
                  "page": None, "line": line, "occurrence": occurrence,
                  "heading": heading, "caption": unescape(alt)[:1000], "reference": reference,
                  "nearby_text": "\n".join(visible[max(0, line - 2):line + 1])[:1800]}
        try:
            target = unquote(unescape(reference))
            windows_drive = bool(re.match(r"^[A-Za-z]:[\\/]", target))
            if (not target or (urlsplit(target).scheme and not windows_drive)
                    or target.startswith(("//", "\\\\")) or "\x00" in target):
                raise ValueError("外链、内联数据或缺失图片引用未读取")
            candidate = Path(target)
            if not candidate.is_absolute():
                candidate = path.parent / candidate
            candidate = candidate.resolve()
            if not any(_within(candidate, root) for root in roots):
                raise ValueError("图片引用越出允许的本地目录")
            if not candidate.is_file() or candidate.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("本地图片缺失或超过大小上限")
            source["image_path"] = str(candidate)
            _emit(result, candidate.read_bytes(), output, source)
        except _ExtractionLimit:
            raise
        except Exception as exc:
            _warning(result, "image_unavailable", str(exc)[:240], **source)


def extract_document_assets(source_path, *, document_id: str, output_dir,
                            allowed_image_roots=()) -> dict:
    """Extract only readable local PNG/JPEG/WebP images, with stable occurrence IDs.

    ``output_dir`` is mandatory and must be chosen by the caller. Markdown may
    reference its source directory and explicitly supplied ``allowed_image_roots``
    after symlink resolution. Failures are returned in ``warnings``; already
    extracted assets remain usable unless the original changed during extraction.
    This is NOT an approval API: no ``approved`` field is inferred or returned.
    """
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("必须提供明确document_id")
    if output_dir is None or not str(output_dir).strip():
        raise ValueError("必须提供明确的图片输出目录")
    path = _local_path(source_path)
    output = _local_path(output_dir)
    if path == output or output.is_file():
        raise ValueError("图片输出目录不能覆盖原文件")
    result = {"version": VERSION, "document_id": document_id, "source_path": str(path),
              "source_sha256": None, "assets": [], "warnings": [], "incomplete": False}
    try:
        result["source_sha256"] = _file_sha(path)
        output.mkdir(parents=True, exist_ok=True)
        extension = path.suffix.lower()
        if extension in (".docx", ".pptx"):
            with zipfile.ZipFile(path) as archive:
                names = _archive_check(archive)
                (_docx if extension == ".docx" else _pptx)(result, archive, names, output)
                if any("/embeddings/" in name for name in names):
                    _warning(result, "embedded_object_unavailable", "嵌入对象/附件未作为图片读取；须另行导入实际正文")
        elif extension == ".pdf":
            _pdf(result, path, output)
        elif extension in (".md", ".markdown"):
            _markdown(result, path, output, (path.parent,) + tuple(_local_path(r) for r in allowed_image_roots))
        else:
            raise ValueError("仅支持DOCX、PPTX、PDF及Markdown资料")
    except Exception as exc:
        _warning(result, "extraction_incomplete", str(exc)[:300])
    if result["source_sha256"]:
        try:
            if _file_sha(path) != result["source_sha256"]:
                result["assets"] = []
                _warning(result, "source_changed", "提取期间原件内容发生变化，本轮图片不得作为该版本依据")
        except Exception as exc:
            result["assets"] = []
            _warning(result, "source_changed", "原件版本无法重新核对，本轮图片不作为可用依据：" + str(exc)[:180])
    result.pop("_attempts", None)
    result.pop("_output_bytes", None)
    return result


from functools import lru_cache


@lru_cache(maxsize=256)
def _verified_png_size(path, expected_sha256):
    """Decode once per immutable PNG version; never re-encode validation input."""
    from PIL import Image
    data=Path(path).read_bytes()
    if len(data)>MAX_IMAGE_BYTES or _sha(data)!=expected_sha256:
        raise ValueError('图片在校验期间发生变化')
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as raster:
            width,height=raster.size
            if (raster.format!='PNG' or not width or not height or width*height>MAX_IMAGE_PIXELS
                    or max(width,height)>MAX_IMAGE_DIMENSION or getattr(raster,'n_frames',1)!=1):
                raise ValueError('图片实际格式、帧数或尺寸无效')
            raster.load()
            return width,height


def verified_asset_path(asset: dict, *, asset_root) -> Path:
    """Validate a selected stored asset before Word embedding; never approve it.

    The caller must resolve ``asset_id`` from its own trusted manifest/evidence
    record, not accept a model-provided path or a model-provided asset dictionary.
    """
    asset_id = asset.get("asset_id", "")
    if not re.fullmatch(r"asset-[0-9a-f]{64}", asset_id):
        raise ValueError("图片ID无效")
    root = _local_path(asset_root)
    path = _local_path(asset.get("path", ""))
    if not _within(path, root) or path.name != asset_id + ".png":
        raise ValueError("图片不在明确的资源目录或文件名不匹配")
    if asset.get("mime") != "image/png" or _file_sha(path, MAX_IMAGE_BYTES) != asset.get("sha256"):
        raise ValueError("图片类型或内容hash与记录不一致")
    width,height=_verified_png_size(str(path),asset['sha256'])
    if (width, height) != (asset.get("width"), asset.get("height")):
        raise ValueError("图片实际格式或尺寸与记录不一致")
    return path

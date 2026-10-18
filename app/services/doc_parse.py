"""资料解析：把上传的参考文件抽成纯文本，喂给大纲生成。

A+B 落地：`.txt/.md` 直接解码；`.docx` 走 python-docx；`.pdf` 走 pdfminer.six。
解析器**惰性导入**——未装依赖时仅该格式降级为"读取失败"，不阻断整个应用启动。
所有异常都收敛成可读结果（(text, error)），绝不向上抛（会话/任务总能给用户答复）。
"""
from __future__ import annotations

from pathlib import Path

# 支持抽取正文的资料类型（图片类不在内，PPT 参考资料只做文本抽取）
TEXT_EXTS = {".txt", ".md", ".markdown", ".csv"}
DOCX_EXTS = {".docx"}
PDF_EXTS = {".pdf"}
SUPPORTED_EXTS = TEXT_EXTS | DOCX_EXTS | PDF_EXTS


def _decode_text(data: bytes) -> str:
    """尽力把字节解成字符串：utf-8 → 去 BOM → gbk → latin-1（永不抛）。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="ignore")


def _docx_text(data: bytes) -> str:
    """python-docx 抽段落 + 表格文本（惰性导入）。"""
    import io

    from docx import Document  # python-docx

    doc = Document(io.BytesIO(data))
    parts: list[str] = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pdf_text(data: bytes) -> str:
    """pdfminer.six 抽 PDF 文本（惰性导入）。"""
    import io

    from pdfminer.high_level import extract_text

    return extract_text(io.BytesIO(data)) or ""


def extract_text(filename: str, data: bytes) -> tuple[str, str]:
    """从 (文件名, 字节) 抽正文，返回 (文本, 错误)。

    - 成功：error 为空串；
    - 不支持的扩展名 / 解析异常 / 依赖缺失：text 空、error 给出人类可读原因。
    """
    ext = Path(filename or "").suffix.lower()
    if not data:
        return "", "文件为空"
    try:
        if ext in TEXT_EXTS:
            return _decode_text(data).strip(), ""
        if ext in DOCX_EXTS:
            return _docx_text(data).strip(), ""
        if ext in PDF_EXTS:
            return _pdf_text(data).strip(), ""
    except ImportError as exc:  # 解析依赖未安装（离线镜像缺包）
        return "", f"{ext} 解析依赖缺失：{exc}"
    except Exception as exc:  # noqa: BLE001 - 单个文件解析失败不影响其它资料
        return "", f"{ext} 解析失败：{type(exc).__name__}"
    return "", f"暂不支持该资料格式：{ext or '未知'}"

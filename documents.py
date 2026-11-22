"""Local document text extraction: no network calls, originals are never written to disk.

Transport-neutral on purpose: Web uses it now, Telegram can reuse extract() later.
Image/scan text comes from RapidOCR (English + Traditional Chinese) running on this machine.
"""
import io
import statistics
import threading
import zipfile

import storage

# One place for every limit. Override any of them under "upload_limits" in data/settings.json.
DEFAULT_LIMITS = {
    'max_bytes': 10 * 1024 * 1024,         # per file
    'max_pdf_pages': 20,
    'max_attachments': 5,                  # per message
    'max_chars_per_attachment': 120_000,   # extracted text
    'max_total_chars': 200_000,            # all attachments of one message
    'max_image_pixels': 40_000_000,
}
PDF_TEXT_MIN_CHARS = 20          # fewer characters on a page means it is a scan: OCR it
PDF_RENDER_DPI = 200
LOW_CONFIDENCE = 0.80
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.heic', '.heif'}
TEXT_EXTENSIONS = {'.txt', '.md', '.csv'}
SUPPORTED = sorted(IMAGE_EXTENSIONS | TEXT_EXTENSIONS | {'.pdf', '.docx'})


class DocumentError(Exception):
    """Safe, user-facing extraction failure."""


def limits():
    result = dict(DEFAULT_LIMITS)
    try:
        override = storage.settings().get('upload_limits') or {}
    except (OSError, ValueError):
        override = {}
    for key, value in (override.items() if isinstance(override, dict) else []):
        if key in result and type(value) is int and value > 0:
            result[key] = value
    return result


_ocr = None
_ocr_lock = threading.Lock()  # CPU-heavy: one recognition at a time


def _engine():
    global _ocr
    if _ocr is None:
        try:
            from rapidocr import RapidOCR, LangRec, ModelType, OCRVersion
            _ocr = RapidOCR(params={'Rec.lang_type': LangRec.CHINESE_CHT, 'Rec.model_type': ModelType.MOBILE,
                                    'Rec.ocr_version': OCRVersion.PPOCRV4})
        except Exception as exc:  # missing package or first-run model download failure
            raise DocumentError('OCR is unavailable. Install requirements and allow the one-time model download.') from exc
    return _ocr


def ocr_image(image):
    """Return (text, mean_confidence) for a PIL image, lines in reading order."""
    import numpy
    with _ocr_lock:
        try:
            result = _engine()(numpy.asarray(image.convert('RGB')))
        except DocumentError:
            raise
        except Exception as exc:
            raise DocumentError('OCR failed on this image.') from exc
    if result is None or result.boxes is None or not len(result.txts or ()):
        return '', 0.0
    items = []
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append({'top': min(ys), 'mid': (min(ys) + max(ys)) / 2, 'left': min(xs), 'h': max(ys) - min(ys),
                      'text': str(text), 'score': float(score)})
    tolerance = max(4.0, statistics.median(item['h'] for item in items) * 0.5)
    items.sort(key=lambda item: (item['mid'], item['left']))
    lines, current = [], []
    for item in items:  # merge boxes on the same visual line, then order left to right
        if current and abs(item['mid'] - current[0]['mid']) > tolerance:
            lines.append(current)
            current = []
        current.append(item)
    if current:
        lines.append(current)
    text = '\n'.join(' '.join(i['text'] for i in sorted(line, key=lambda i: i['left'])) for line in lines)
    return text, sum(item['score'] for item in items) / len(items)


def _open_image(data, cap):
    from PIL import Image, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = None  # we enforce our own cap below
    try:
        import pillow_heif  # iPhone HEIC/HEIF photos; harmless for other formats
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DocumentError('This image could not be read.') from exc
    if image.width * image.height > cap:
        raise DocumentError(f'Image is too large ({image.width}x{image.height} pixels).')
    return image


def _decode_text(data):
    for encoding in ('utf-8-sig', 'big5', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('cp1252', errors='replace')


def _docx_text(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > 200 * 1024 * 1024:
                raise DocumentError('This document expands to an unsafe size.')
        from docx import Document
        document = Document(io.BytesIO(data))
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError('This Word document could not be read.') from exc
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(' | '.join(cell.text.strip() for cell in row.cells))
    return '\n'.join(part for part in parts if part.strip())


def _pdf_text(data, lim):
    import pypdf
    import pypdfium2
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(''):
            raise DocumentError('This PDF is password protected.')
        count = len(reader.pages)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError('This PDF could not be read.') from exc
    if count > lim['max_pdf_pages']:
        raise DocumentError(f'This PDF has {count} pages; the current limit is {lim["max_pdf_pages"]}.')
    pages, scans, confidences = [], 0, []
    renderer = None
    try:
        for number in range(count):  # one page at a time keeps memory bounded
            try:
                text = (reader.pages[number].extract_text() or '').strip()
            except Exception:
                text = ''
            if len(text) < PDF_TEXT_MIN_CHARS:
                if renderer is None:
                    renderer = pypdfium2.PdfDocument(data)
                bitmap = renderer[number].render(scale=PDF_RENDER_DPI / 72)
                image = bitmap.to_pil()
                if image.width * image.height > lim['max_image_pixels']:
                    raise DocumentError(f'Page {number + 1} is too large to scan.')
                text, confidence = ocr_image(image)
                if text.strip():
                    scans += 1
                    confidences.append(confidence)
            pages.append(text.strip())
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError('This PDF could not be read.') from exc
    finally:
        if renderer is not None:
            renderer.close()
    body = '\n\n'.join(f'[Page {n + 1}]\n{t}' for n, t in enumerate(pages) if t) if count > 1 else (pages[0] if pages else '')
    return body, count, scans, (sum(confidences) / len(confidences) if confidences else None)


def extract(filename, data, lim=None):
    """Return {'name','kind','pages','chars','text','warnings'}. kind is 'exact' or 'ocr'."""
    lim = lim or limits()
    name = (filename or 'file').replace('\\', '/').rsplit('/', 1)[-1][:120] or 'file'
    extension = '.' + name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if extension not in SUPPORTED:
        raise DocumentError(f'{name}: unsupported file type. Supported: {", ".join(SUPPORTED)}.')
    if len(data) > lim['max_bytes']:
        raise DocumentError(f'{name}: file is {len(data) / 1048576:.1f} MB; the current limit is {lim["max_bytes"] / 1048576:.0f} MB.')
    if not data:
        raise DocumentError(f'{name}: the file is empty.')
    warnings, kind, pages, confidence = [], 'exact', 1, None
    source = 'image' if extension in IMAGE_EXTENSIONS else 'pdf' if extension == '.pdf' else 'docx' if extension == '.docx' else 'text'
    try:
        if extension in IMAGE_EXTENSIONS:
            text, confidence = ocr_image(_open_image(data, lim['max_image_pixels']))
            kind = 'ocr'
        elif extension == '.pdf':
            if not data.lstrip()[:5].startswith(b'%PDF'):
                raise DocumentError('This is not a valid PDF.')
            text, pages, scans, confidence = _pdf_text(data, lim)
            if scans:
                kind = 'ocr'
                warnings.append(f'{scans} of {pages} page(s) had no text layer and were read with OCR.')
        elif extension == '.docx':
            if data[:2] != b'PK':
                raise DocumentError('This is not a valid Word document.')
            text = _docx_text(data)
        else:
            text = _decode_text(data)
    except DocumentError as exc:
        raise DocumentError(f'{name}: {exc}') from None
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text:
        raise DocumentError(f'{name}: no readable text was found.')
    if len(text) > lim['max_chars_per_attachment']:
        raise DocumentError(f'{name}: {len(text):,} characters extracted; the current limit is {lim["max_chars_per_attachment"]:,}.')
    if kind == 'ocr' and confidence is not None and confidence < LOW_CONFIDENCE:
        warnings.append(f'OCR confidence is low ({confidence:.0%}). Check the text for mistakes.')
    return {'name': name, 'kind': kind, 'source': source, 'pages': pages, 'chars': len(text), 'text': text, 'warnings': warnings}


def compose_message(typed, attachments):
    """Fold confirmed attachment text into one message body. Privacy processing runs on the result."""
    blocks = [f'[Attached file: {a["name"]}]\n{a["text"].strip()}\n[End of attached file]' for a in attachments]
    return '\n\n'.join(part for part in [typed.strip(), *blocks] if part)

import asyncio
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

import app
import documents
import engine
import storage
from privacy_engine import PrivacyEngine

FONTS = Path(r'C:\Windows\Fonts')
CJK_FONT = next((p for p in (FONTS / 'msjh.ttc', FONTS / 'mingliu.ttc') if p.exists()), None)


class FakeAnalyzer:
    def analyze(self, text, language):
        return [SimpleNamespace(start=m.start(), end=m.end(), score=.99, entity_type='EMAIL_ADDRESS')
                for m in re.finditer(r'[\w.]+@[\w.]+\.[a-z]+', text)]


def sample_image(lines=('Meeting notes: invoice 4021 approved by Sarah Lim', 'Action: send revised forecast to finance team'), cjk=None):
    image = Image.new('RGB', (1100, 100 * (len(lines) + (1 if cjk else 0)) + 40), 'white')
    draw = ImageDraw.Draw(image)
    latin = ImageFont.truetype(str(FONTS / 'arial.ttf'), 40) if (FONTS / 'arial.ttf').exists() else ImageFont.load_default(40)
    y = 20
    if cjk:
        draw.text((30, y), cjk, fill='black', font=ImageFont.truetype(str(CJK_FONT), 40))
        y += 90
    for line in lines:
        draw.text((30, y), line, fill='black', font=latin)
        y += 90
    return image


def encode(image, fmt):
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def text_pdf(text):
    """A minimal one-page PDF with a real text layer."""
    stream = f'BT /F1 18 Tf 72 700 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
               b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
    start = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode()
    for offset in offsets:
        out += f'{offset:010d} 00000 n \n'.encode()
    out += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()
    return bytes(out)


class ExtractionTests(unittest.TestCase):
    def test_plain_text_encodings_and_docx_are_exact(self):
        self.assertEqual(documents.extract('a.txt', 'Hello 你好'.encode('utf-8'))['text'], 'Hello 你好')
        big5 = documents.extract('b.txt', '會議紀要 預算'.encode('big5'))
        self.assertEqual((big5['text'], big5['kind'], big5['source']), ('會議紀要 預算', 'exact', 'text'))
        from docx import Document
        document = Document()
        document.add_paragraph('Quarterly review')
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text, table.rows[0].cells[1].text = 'Owner', 'Ann'
        buffer = io.BytesIO(); document.save(buffer)
        result = documents.extract('plan.docx', buffer.getvalue())
        self.assertIn('Quarterly review', result['text']); self.assertIn('Owner | Ann', result['text'])
        self.assertEqual((result['kind'], result['source']), ('exact', 'docx'))

    def test_digital_pdf_uses_text_layer_without_ocr(self):
        with patch.object(documents, 'ocr_image', side_effect=AssertionError('OCR must not run')):
            result = documents.extract('digital.pdf', text_pdf('Digital PDF text layer is present here'))
        self.assertEqual((result['kind'], result['source'], result['pages']), ('exact', 'pdf', 1))
        self.assertIn('Digital PDF text layer', result['text'])

    def test_image_and_scanned_pdf_are_read_with_ocr(self):
        image = sample_image()
        png = documents.extract('scan.png', encode(image, 'PNG'))
        self.assertEqual((png['kind'], png['source']), ('ocr', 'image'))
        self.assertIn('invoice 4021 approved by Sarah Lim', png['text'])
        self.assertIn('revised forecast', png['text'])
        scanned = documents.extract('scan.pdf', encode(image, 'PDF'))
        self.assertEqual((scanned['kind'], scanned['pages']), ('ocr', 1))
        self.assertIn('invoice 4021', scanned['text'])
        self.assertTrue(any('OCR' in w for w in scanned['warnings']))

    @unittest.skipUnless(CJK_FONT, 'No Traditional Chinese system font')
    def test_traditional_chinese_ocr(self):
        result = documents.extract('zh.png', encode(sample_image(cjk='會議紀要：預算審查與合約續約'), 'PNG'))
        self.assertIn('預算審查', result['text'])
        self.assertIn('invoice 4021', result['text'])

    def test_limits_and_rejections_give_clear_errors(self):
        tight = dict(documents.DEFAULT_LIMITS, max_bytes=10, max_pdf_pages=1, max_chars_per_attachment=5)
        cases = [('big.txt', b'x' * 11, 'limit is'), ('x.exe', b'abc', 'unsupported'), ('e.txt', b'', 'empty'),
                 ('fake.pdf', b'not a pdf', 'not a valid PDF'), ('bad.png', b'nope', 'could not be read'),
                 ('long.txt', b'123456', 'characters extracted'), ('blank.txt', b'   \n', 'no readable text')]
        for name, data, fragment in cases:
            with self.subTest(name):
                with self.assertRaisesRegex(documents.DocumentError, fragment):
                    documents.extract(name, data, tight if name != 'blank.txt' else documents.DEFAULT_LIMITS)
        pages = [Image.new('RGB', (50, 50), 'white')] * 2
        buffer = io.BytesIO(); pages[0].save(buffer, 'PDF', save_all=True, append_images=pages[1:])
        with self.assertRaisesRegex(documents.DocumentError, '2 pages; the current limit is 1'):
            documents.extract('two.pdf', buffer.getvalue(), dict(documents.DEFAULT_LIMITS, max_pdf_pages=1))
        with self.assertRaisesRegex(documents.DocumentError, 'too large'):
            documents.extract('huge.png', encode(Image.new('RGB', (100, 100)), 'PNG'), dict(documents.DEFAULT_LIMITS, max_image_pixels=50))

    def test_limits_are_overridable_from_settings(self):
        with patch.object(storage, 'settings', return_value={'upload_limits': {'max_pdf_pages': 99, 'max_bytes': 'bad', 'unknown': 1}}):
            lim = documents.limits()
        self.assertEqual(lim['max_pdf_pages'], 99)
        self.assertEqual(lim['max_bytes'], documents.DEFAULT_LIMITS['max_bytes'])
        self.assertNotIn('unknown', lim)

    def test_compose_message(self):
        self.assertEqual(documents.compose_message('Summarise', [{'name': 'a.txt', 'text': ' body '}]),
                         'Summarise\n\n[Attached file: a.txt]\nbody\n[End of attached file]')
        self.assertTrue(documents.compose_message('', [{'name': 'a.txt', 'text': 'x'}]).startswith('[Attached file'))


class UploadApiTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': ''})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in [('DATA', root / 'data'), ('TERMS', root / 'sensitive_terms.txt'), ('CONFIG', root / 'config.json')]:
            change = patch.object(storage, name, value)
            change.start()
            self.addCleanup(change.stop)
        self.privacy = PrivacyEngine(FakeAnalyzer())
        change = patch.object(app, 'PrivacyEngine', return_value=self.privacy)
        change.start()
        self.addCleanup(change.stop)
        app.chat_lock = asyncio.Lock()
        self.client = TestClient(app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': 'CLIENT: Acme'})

    def test_upload_review_flag_only_for_pdf_and_images_and_follows_setting(self):
        files = [('files', ('note.txt', b'Plain note', 'text/plain')), ('files', ('p.pdf', text_pdf('Digital PDF text layer is present here'), 'application/pdf'))]
        first = self.client.post('/api/upload', files=files).json()
        self.assertEqual([f['review'] for f in first['files']], [False, False])
        self.client.put('/api/settings', json={'model': 'test/model', 'sensitive_terms': '', 'review_extracted_text': True})
        self.assertTrue(self.client.get('/api/settings').json()['review_extracted_text'])
        second = self.client.post('/api/upload', files=files).json()
        self.assertEqual([f['review'] for f in second['files']], [False, True])
        self.assertEqual(second['limits']['max_attachments'], 5)

    def test_upload_errors_are_422_with_filename_and_nothing_is_stored(self):
        response = self.client.post('/api/upload', files=[('files', ('x.exe', b'abc', 'application/octet-stream'))])
        self.assertEqual(response.status_code, 422)
        self.assertIn('x.exe', response.json()['detail'])
        many = [('files', (f'{i}.txt', b'x', 'text/plain')) for i in range(6)]
        self.assertEqual(self.client.post('/api/upload', files=many).status_code, 422)
        self.assertFalse((storage.DATA / 'sessions').exists() and any((storage.DATA / 'sessions').iterdir()))

    def test_send_with_attachments_masks_text_saves_metadata_and_enforces_limits(self):
        sent = []

        async def stream(messages, model, max_tokens=None):
            sent.append(messages)
            yield 'Got it'
        attachment = {'name': 'brief.txt', 'kind': 'exact', 'text': 'Acme owes jane@example.com a reply'}
        with patch.object(engine, 'stream_complete', stream):
            response = self.client.post('/api/chat/stream', json={'message': '', 'attachments': [attachment]})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        outbound = json.dumps(sent)
        self.assertNotIn('Acme', outbound); self.assertNotIn('jane@example.com', outbound)
        self.assertIn('[Attached file: brief.txt]', outbound)
        user = events[-1]['session']['messages'][0]
        self.assertEqual(user['typed'], '')
        self.assertEqual(user['attachments'], [{'name': 'brief.txt', 'kind': 'exact', 'chars': len(attachment['text'])}])
        self.assertIn('Acme owes', user['content'])  # real text stays local
        self.assertEqual(self.client.post('/api/chat/stream', json={'message': '  '}).status_code, 422)
        too_many = [dict(attachment, name=f'{i}.txt') for i in range(6)]
        self.assertEqual(self.client.post('/api/chat/stream', json={'message': 'x', 'attachments': too_many}).status_code, 422)
        with patch.object(documents, 'limits', return_value=dict(documents.DEFAULT_LIMITS, max_total_chars=10)):
            self.assertEqual(self.client.post('/api/chat/stream', json={'message': 'x', 'attachments': [attachment]}).status_code, 422)


if __name__ == '__main__':
    unittest.main()

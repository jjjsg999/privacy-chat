import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import app


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_env_is_loaded_before_telegram(self):
        @asynccontextmanager
        async def telegram(privacy, lock):
            self.assertEqual(os.environ['TELEGRAM_BOT_TOKEN'], 'test-token')
            self.assertEqual(os.environ['TELEGRAM_ALLOWED_USER_IDS'], '456')
            yield

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '.env').write_text(
                'TELEGRAM_BOT_TOKEN=test-token\nTELEGRAM_ALLOWED_USER_IDS=123\n',
                encoding='utf-8-sig')
            with (patch.object(app.storage, 'ROOT', root),
                  patch.object(app.storage, 'DATA', root / 'data'),
                  patch.dict(os.environ, {'TELEGRAM_ALLOWED_USER_IDS': '456'}, clear=True),
                  patch.object(app, 'PrivacyEngine', return_value=object()),
                  patch.object(app, 'telegram_lifespan', telegram)):
                async with app.lifespan(app.app):
                    pass

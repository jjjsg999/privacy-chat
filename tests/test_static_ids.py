"""Every element id that the chat and settings scripts look up exists in their page. Offline, text only."""
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / 'static'
PAGES = {'index.html': ['chat.js', 'web-search.js', 'sent-preview.js'],
         'settings.html': ['settings.js', 'web-search-settings.js', 'spaces-settings.js']}
LOOKUP = re.compile(r"""(?:\$|getElementById)\(\s*['"]([\w-]+)['"]\s*\)""")


def ids_in(page):
    return set(re.findall(r'\bid="([\w-]+)"', (STATIC / page).read_text(encoding='utf-8')))


class StaticIdTests(unittest.TestCase):
    def test_looked_up_ids_exist(self):
        for page, scripts in PAGES.items():
            present = ids_in(page)
            for script in scripts:
                text = (STATIC / script).read_bytes().decode('utf-8', errors='replace')  # settings.js is not valid UTF-8
                for found in sorted(set(LOOKUP.findall(text))):
                    with self.subTest(page=page, script=script, id=found):
                        self.assertIn(found, present)


if __name__ == '__main__':
    unittest.main()

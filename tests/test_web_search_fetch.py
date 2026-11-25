"""Engines and page reading: SSRF guard, limits, snippet fallback, trafilatura. No test touches the network."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

import web_search
from web_search import SearchError

BRAVE_FIXTURE = {  # hand-written, shaped like Brave's web search response
    'type': 'search', 'query': {'original': 'MAS capital adequacy requirements'},
    'web': {'type': 'search', 'results': [
        {'title': 'Capital <strong>adequacy</strong> rules', 'url': 'https://www.example.gov/capital',
         'description': 'The <strong>MAS</strong> sets minimum ratios &amp; buffers.'},
        {'title': 'Basel III overview', 'url': 'https://www.example.org/basel', 'description': 'Standards for banks.'},
        {'title': 'Not a web link', 'url': 'javascript:alert(1)', 'description': 'dropped'},
    ]}}

PAGE = ('<html><head><title>Rules</title><script>alert(1)</script></head><body><nav>Menu Home About</nav>'
        '<article><h1>Capital rules for banks</h1><p>Banks must hold a minimum amount of capital against the loans '
        'they make, measured against risk-weighted assets. The regulator publishes the ratios and the buffers that '
        'apply on top of them, and reviews them every year after consulting the industry.</p><p>Larger banks face '
        'extra requirements because their failure would affect the wider financial system.</p></article>'
        '<footer>Copyright</footer></body></html>')


def run(coro):
    return asyncio.run(coro)


def public(*args):
    async def resolve(host, port):
        return ['93.184.216.34']
    return resolve


class GuardTests(unittest.TestCase):
    def blocked(self, url, resolved=('93.184.216.34',)):
        async def resolve(host, port):
            return list(resolved)
        with patch.object(web_search, 'resolve_host', resolve):
            with self.assertRaises(SearchError) as caught:
                run(web_search.check_url(url))
        self.assertEqual(str(caught.exception), 'blocked address')

    def test_private_and_local_addresses_are_refused(self):
        for url in ['http://127.0.0.1/', 'http://localhost/', 'http://[::1]/', 'http://10.1.2.3/', 'http://172.16.0.5/',
                    'http://192.168.1.10/', 'http://169.254.169.254/latest/meta-data', 'http://0.0.0.0/',
                    'http://127.0.0.1:8000/api/sessions', 'http://[::ffff:127.0.0.1]/', 'http://224.0.0.1/',
                    'ftp://example.org/', 'file:///etc/passwd', 'http://user:pw@example.org/', 'http://app.localhost/']:
            with self.subTest(url=url):
                self.blocked(url)

    def test_names_resolving_to_private_addresses_are_refused(self):
        for address in ['127.0.0.1', '10.0.0.8', '192.168.0.2', '::1', 'fe80::1', '172.20.1.1']:
            with self.subTest(address=address):
                self.blocked('https://looks-public.example/', [address])
        self.blocked('https://mixed.example/', ['93.184.216.34', '10.0.0.1'])  # every address must be public

    def test_public_address_passes(self):
        with patch.object(web_search, 'resolve_host', public()):
            run(web_search.check_url('https://www.example.org/page'))


class FetchTests(unittest.TestCase):
    def fetch(self, handler, url='https://www.example.org/start'):
        async def go():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                return await web_search.fetch_page(url, client)

        async def resolve(host, port):
            return ['10.0.0.9'] if host == 'internal.example' else ['93.184.216.34']
        with patch.object(web_search, 'resolve_host', resolve):
            return run(go())

    def test_html_page_is_read(self):
        page = self.fetch(lambda r: httpx.Response(200, text=PAGE, headers={'content-type': 'text/html; charset=utf-8'}))
        self.assertIn('Capital rules', page)

    def test_redirect_to_a_private_address_is_refused(self):
        def handler(request):
            if request.url.host == 'www.example.org':
                return httpx.Response(302, headers={'location': 'http://internal.example/admin'})
            raise AssertionError('the private host must never be requested')
        with self.assertRaises(SearchError):
            self.fetch(handler)

    def test_redirect_to_loopback_literal_is_refused(self):
        seen = []

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(301, headers={'location': 'http://127.0.0.1:8000/api/settings'})
        with self.assertRaises(SearchError):
            self.fetch(handler)
        self.assertEqual(len(seen), 1)

    def test_public_redirects_are_followed_up_to_three(self):
        hops = []

        def handler(request):
            hops.append(request.url.path)
            if len(hops) <= 3:
                return httpx.Response(302, headers={'location': f'/hop{len(hops)}'})
            return httpx.Response(200, text=PAGE, headers={'content-type': 'text/html'})
        self.assertIn('Capital', self.fetch(handler))

        hops.clear()
        with self.assertRaises(SearchError) as caught:
            self.fetch(lambda r: hops.append(1) or httpx.Response(302, headers={'location': '/again'}))
        self.assertEqual(str(caught.exception), 'too many redirects')

    def test_type_and_size_limits(self):
        with self.assertRaises(SearchError):
            self.fetch(lambda r: httpx.Response(200, content=b'%PDF', headers={'content-type': 'application/pdf'}))
        big = b'<html>' + b'a' * (web_search.MAX_PAGE_BYTES + 10)
        with self.assertRaises(SearchError) as caught:
            self.fetch(lambda r: httpx.Response(200, content=big, headers={'content-type': 'text/html'}))
        self.assertEqual(str(caught.exception), 'page too large')

    def test_http_errors_name_only_the_code(self):
        with self.assertRaises(SearchError) as caught:
            self.fetch(lambda r: httpx.Response(503, text='provider secret text'))
        self.assertEqual(str(caught.exception), 'HTTP 503')


class ExtractTests(unittest.TestCase):
    def test_trafilatura_keeps_the_article_and_drops_scripts(self):
        text = web_search.extract_text(PAGE)
        self.assertIn('risk-weighted assets', text)
        self.assertNotIn('alert(1)', text)
        self.assertNotIn('<p>', text)

    def test_read_sources_caps_and_falls_back_to_snippets(self):
        results = [{'title': 'One', 'url': 'https://a.example/1', 'snippet': 'snippet one'},
                   {'title': 'Two', 'url': 'https://a.example/2', 'snippet': 'snippet two'},
                   {'title': 'Three', 'url': 'https://a.example/3', 'snippet': 'snippet three'}]

        async def fetch(url, client):
            if url.endswith('/2'):
                raise SearchError('timeout')
            return PAGE.replace('Banks must', 'Banks must ' + 'really ' * 2000)
        with patch.object(web_search, 'fetch_page', fetch):
            sources, read = run(web_search.read_sources(results, 2, 'lean', 'brave'))
        self.assertEqual(read, 1)
        self.assertEqual(len(sources[0]['text']), 2000)       # lean cap
        self.assertEqual(sources[1]['text'], 'snippet two')    # failed page -> snippet
        self.assertEqual(sources[2]['text'], 'snippet three')  # beyond pages_per_search -> snippet

    def test_zero_pages_means_snippets_only(self):
        async def fetch(url, client):
            raise AssertionError('no page may be read')
        with patch.object(web_search, 'fetch_page', fetch):
            sources, read = run(web_search.read_sources([{'title': 'T', 'url': 'https://a.example', 'snippet': 's'}], 0, 'full', 'brave'))
        self.assertEqual((read, sources[0]['text']), (0, 's'))


class EngineTests(unittest.TestCase):
    def brave(self, handler):
        original = httpx.AsyncClient
        with patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': 'test-key-not-real'}), \
                patch.object(web_search.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            return run(web_search.search('brave', 'MAS capital adequacy requirements'))

    def test_brave_request_and_parsing(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json=BRAVE_FIXTURE)
        results = self.brave(handler)
        self.assertEqual(seen[0].url.host, 'api.search.brave.com')
        self.assertEqual(seen[0].headers['X-Subscription-Token'], 'test-key-not-real')
        self.assertEqual(seen[0].url.params['count'], '5')
        self.assertEqual(results[0], {'title': 'Capital adequacy rules', 'url': 'https://www.example.gov/capital',
                                      'snippet': 'The MAS sets minimum ratios & buffers.'})
        self.assertEqual(len(results), 2)  # the javascript: link is dropped

    def test_brave_errors_never_echo_provider_text(self):
        with self.assertRaises(SearchError) as caught:
            self.brave(lambda r: httpx.Response(429, text='rate limited: detailed provider message'))
        self.assertEqual(str(caught.exception), 'HTTP 429')

    def test_brave_needs_a_key(self):
        with patch.dict('os.environ', {'BRAVE_SEARCH_API_KEY': ''}):
            with self.assertRaises(SearchError):
                run(web_search.search('brave', 'q'))

    def test_fake_engine_only_with_the_environment_switch(self):
        with patch.dict('os.environ', {'PRIVACY_LLM_FAKE_SEARCH': ''}):
            with self.assertRaises(SearchError):
                run(web_search.search('fake', 'bank news'))
            self.assertNotIn('fake', web_search.engine_status())
        with patch.dict('os.environ', {'PRIVACY_LLM_FAKE_SEARCH': '1'}):
            self.assertEqual(len(run(web_search.search('fake', 'bank news'))), 5)
            self.assertIn('fake', web_search.engine_status())
            self.assertEqual(web_search.active_engine({'engine': 'brave'}), 'fake')

    def test_ddgs_missing_is_a_clean_failure(self):
        with patch.dict('sys.modules', {'ddgs': None}):
            with self.assertRaises(SearchError):
                run(web_search.search('ddgs', 'bank news'))


if __name__ == '__main__':
    unittest.main()

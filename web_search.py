"""Web search for web chats: query check, search engines, page reading. No model calls happen here.

The pipeline itself (planner call, budget, session records, answer) lives in engine.py.
"""
import importlib.util
import os

ENGINE_LABELS = {'brave': 'Brave Search', 'ddgs': 'ddgs', 'fake': 'Fake search'}


def fake_enabled():
    return os.environ.get('PRIVACY_LLM_FAKE_SEARCH') == '1'


def brave_key():
    return os.environ.get('BRAVE_SEARCH_API_KEY', '').strip()


def engine_status():
    """What Settings shows about each engine. Never the key itself."""
    ddgs = importlib.util.find_spec('ddgs') is not None
    status = {
        'brave': {'label': ENGINE_LABELS['brave'], 'available': bool(brave_key()),
                  'detail': 'key saved' if brave_key() else 'no key'},
        'ddgs': {'label': ENGINE_LABELS['ddgs'], 'available': ddgs,
                 'detail': 'no key' if ddgs else 'not installed (pip install ddgs)'},
    }
    if fake_enabled():
        status['fake'] = {'label': ENGINE_LABELS['fake'], 'available': True, 'detail': 'test mode'}
    return status

import asyncio

import httpx
import pytest
from textual.widgets import Button, Input, Markdown, Select, Static

from canvas_rag.answer import generate
from canvas_rag.canvas import Canvas, record
from canvas_rag.config import Config
from canvas_rag.demo import seed
from canvas_rag.ui import Browser, CanvasApp


@pytest.mark.parametrize('url', ['https://remote.example', 'http://192.168.1.2:11434',
                                 'http://localhost@remote.example', 'http://127.0.0.1:0',
                                 'http://127.0.0.1:11434?send=data'])
async def test_ollama_rejects_remote_endpoints_before_network(url, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Unsafe endpoint reached HTTP client')
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    c = Config(provider='ollama', model='sample', ollama=url)
    with pytest.raises(ValueError, match='local address'):
        await generate(c, 'private course data')


@pytest.mark.parametrize('url', ['http://127.0.0.1:11434', 'http://[::1]:11434', 'http://localhost:11434'])
def test_local_ollama_addresses(url):
    Config(ollama=url).validate_ollama()


async def test_ollama_ignores_proxy_environment(monkeypatch):
    original = httpx.AsyncClient
    def respond(request):
        assert b'private-token' not in request.content
        assert b'[redacted]' in request.content
        return httpx.Response(200, json={'message': {'content': 'reply'}})
    def client(**kwargs):
        assert kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    assert await generate(Config(provider='ollama', model='sample', token='private-token'),
                          'question accidentally includes private-token') == 'reply'


async def test_demo_is_offline_and_leaves_credentials_alone(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Demo must not load real credentials or contact any service')
    monkeypatch.setattr(Config, 'load', forbidden)
    monkeypatch.setattr(httpx, 'AsyncClient', forbidden)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    c, db = seed(tmp_path)
    app = CanvasApp(c, db, demo=True)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            app.ask('attendance')
            await app.workers.wait_for_complete()
            assert 'no AI model called' in app.history[-1][1]
            assert db.upcoming() and '92' in db.grades()
            app.run_sync()
            await app.workers.wait_for_complete()
            app.show_setup()
            assert not app.screen.is_modal
            await pilot.click('#grades')
            await pilot.pause()
            assert app.query_one('#question', Input).region.bottom <= 24
        assert not (tmp_path / 'credentials.json').exists()
        assert not (tmp_path / 'config.json').exists()
    finally:
        db.close()


async def test_chat_links_only_open_known_canvas_sources(tmp_path, monkeypatch):
    c, db = seed(tmp_path)
    app = CanvasApp(c, db)
    opened = []
    monkeypatch.setattr(app, 'open_url', opened.append)
    try:
        async with app.run_test() as pilot:
            good = c.url + '/courses/101/pages/syllabus'
            bad = [good + '?leak=grade', good + '#secret', 'file:///etc/passwd',
                   'https://evil.example/steal', c.url + '/courses/101/pages/invented']
            await app.say('[Source](' + good + ')', role='assistant')
            widget = list(app.query(Markdown))[-1]
            for href in bad + [good]:
                widget.post_message(Markdown.LinkClicked(widget, href))
                await pilot.pause()
            assert opened == [good]
    finally:
        db.close()


async def test_browser_filter_and_escape(tmp_path):
    c, db = seed(tmp_path)
    app = CanvasApp(c, db, demo=True)
    try:
        async with app.run_test(size=(80, 24)) as pilot:
            app.action_browse()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, Browser)
            screen.query_one('#doc-filter', Input).value = 'syllabus'
            await pilot.pause()
            screen.query_one('#doc-picker', Select).value = '101:page:1'
            await pilot.pause()
            assert not screen.query_one('#open-source', Button).disabled
            await pilot.press('escape')
            assert not isinstance(app.screen, Browser)
    finally:
        db.close()


async def test_partial_sync_and_errors_visible_without_token(tmp_path, monkeypatch):
    from canvas_rag import ui
    c, db = seed(tmp_path)
    c.token = 'private-token'
    async def partial(*args):
        db.coverage(101, 'page', 'error', 'Forbidden')
        db.replace(101, 'file', [record(101, 'file', {'id': 8}, c.url,
                                      '[No extractable text; this file may require OCR.]')])
    monkeypatch.setattr(ui, 'sync', partial)
    app = CanvasApp(c, db)
    try:
        async with app.run_test() as pilot:
            app.run_sync()
            await app.workers.wait_for_complete()
            assert '1 incomplete sections, 1 unreadable files' in str(app.query_one('#snapshot', Static).render())
            assert 'Sync complete' not in app.status_text
            async def broken(*args, **kwargs):
                raise RuntimeError('request failed with private-token')
            monkeypatch.setattr(ui, 'answer', broken)
            app.ask('question')
            await app.workers.wait_for_complete()
            await pilot.pause()
            output = '\n'.join(w._markdown for w in app.query(Markdown))
            assert 'private-token' not in output and '[redacted]' in output
    finally:
        db.close()


async def test_expired_canvas_token_has_recovery_instructions():
    config = Config(url='https://canvas.example', token='expired')
    async with Canvas(config, httpx.MockTransport(lambda _: httpx.Response(401))) as api:
        with pytest.raises(RuntimeError, match='Token expired or invalid.*Courses setup'):
            await api.one('users/self/profile')

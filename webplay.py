"""Short-lived browser playback sessions for catalogued routes."""
import hashlib
import re
import secrets
import threading
import time
from collections import OrderedDict
from http.cookiejar import CookieJar
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urljoin, urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

sessions = {}
lock = threading.RLock()

def playback_headers(options):
    base = {'User-Agent': 'Mozilla/5.0 IPTV-Studio/1.0', 'Accept-Encoding': 'identity'}
    manifest, media = dict(base), dict(base)
    names = {'user-agent': 'User-Agent', 'referer': 'Referer', 'referrer': 'Referer', 'origin': 'Origin'}
    def apply(target, name, value):
        key = names.get(name.lower())
        if key and value and not re.search(r'[\r\n]', value):
            target[key] = value
    for option in options:
        vlc = re.match(r'^#EXTVLCOPT:http-([\w-]+)=(.*)$', option, re.I)
        if vlc:
            for target in (manifest, media):
                apply(target, *vlc.groups())
        kodi = re.match(r'^#KODIPROP:inputstream\.adaptive\.(stream|common|manifest)_headers=(.*)$', option, re.I)
        if kodi:
            kind, values = kodi.groups()
            for name, value in parse_qsl(values):
                if kind.lower() != 'stream': apply(manifest, name, value)
                if kind.lower() != 'manifest': apply(media, name, value)
    return manifest, media

def resource(session, address, manifest=False):
    parsed = urlsplit(address)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('网页播放仅支持 HTTP 或 HTTPS 直播地址')
    key = hashlib.sha256((str(manifest)+address).encode()).hexdigest()[:32]
    with lock:
        session['resources'][key] = (address, manifest)
        session['resources'].move_to_end(key)
        while len(session['resources']) > 4096:
            session['resources'].popitem(last=False)
    return '/api/play-media?session='+session['id']+'&resource='+key

def rewrite(session, text, base):
    lines = []
    for line in text.splitlines():
        value = line.strip()
        if value.startswith('#'):
            # URI attributes include keys, initialization maps and alternate tracks.
            manifest = value.startswith(('#EXT-X-MEDIA:', '#EXT-X-I-FRAME-STREAM-INF:', '#EXT-X-RENDITION-REPORT:'))
            value = re.sub(r'URI="([^"]+)"', lambda m: 'URI="'+resource(session, urljoin(base, m[1]), manifest)+'"', value)
        elif value:
            value = resource(session, urljoin(base, value), bool(lines and lines[-1].startswith('#EXT-X-STREAM-INF:')))
        lines.append(value)
    return '\n'.join(lines)+'\n'

def close_session(key):
    with lock:
        session = sessions.pop(key, None)
        if session:
            session['closed'].set()

def new_session(line):
    with lock:
        for key, session in list(sessions.items()):
            if time.monotonic()-session['touched'] > 600:
                close_session(key)
        if len(sessions) >= 32:
            raise ValueError('正在播放的窗口过多，请先关闭其他播放窗口')
        key = secrets.token_urlsafe(24)
        manifest, media = playback_headers(line.get('options', []))
        session = dict(id=key, resources=OrderedDict(), manifest=manifest, media=media,
                       opener=build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar())),
                       closed=threading.Event(), touched=time.monotonic())
        sessions[key] = session
    try:
        address = resource(session, line['url'], True)
        with session['opener'].open(Request(line['url'], headers=manifest), timeout=12) as upstream:
            prefix = upstream.read(1024)
            mime = upstream.headers.get_content_type()
            if prefix.lstrip(b'\xef\xbb\xbf \r\n').startswith(b'#EXTM3U'):
                body = prefix+upstream.read(2*1024*1024)
                if len(body) > 2*1024*1024: raise ValueError('直播清单过大')
                session['initial'] = (body.decode('utf-8-sig'), upstream.url)
                kind = 'hls'
            elif mime in ('video/mp4', 'video/webm') or prefix[4:8] == b'ftyp':
                kind = 'native'
            else:
                raise ValueError('该线路返回的格式暂不支持网页播放，支持 HLS、MP4 和 WebM')
        session['root'] = line['url']
        if kind == 'native': address = resource(session, line['url'])
        return dict(session=key, url=address, kind=kind)
    except Exception:
        close_session(key)
        raise

def serve(handler, query):
    key = query.get('session', [''])[0]
    rid = query.get('resource', [''])[0]
    with lock:
        session = sessions.get(key)
        if not session or time.monotonic()-session['touched'] > 600:
            close_session(key)
            handler.send(410, '播放会话已结束，请重新播放', 'text/plain')
            return
        session['touched'] = time.monotonic()
        item = session['resources'].get(rid)
        if item: session['resources'].move_to_end(rid)
        initial = session.pop('initial', None) if item and item[0] == session['root'] else None
    if not item:
        handler.send(404, '没有此播放资源', 'text/plain')
        return
    if initial:
        handler.send(200, rewrite(session, *initial), 'application/vnd.apple.mpegurl')
        return
    address, manifest = item
    headers = dict(session['manifest'] if manifest else session['media'])
    span = handler.headers.get('Range')
    if span:
        if not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', span):
            handler.send(416, '无效字节范围', 'text/plain'); return
        headers['Range'] = span
    with session['opener'].open(Request(address, headers=headers), timeout=12) as upstream:
        mime = upstream.headers.get_content_type()
        prefix = upstream.read(1024)
        if prefix.lstrip(b'\xef\xbb\xbf \r\n').startswith(b'#EXTM3U'):
            body = prefix+upstream.read(2*1024*1024)
            if len(body) > 2*1024*1024: raise ValueError('直播清单过大')
            handler.send(200, rewrite(session, body.decode('utf-8-sig'), upstream.url), 'application/vnd.apple.mpegurl')
            return
        if mime in ('text/html', 'application/json'):
            raise ValueError('上游返回了错误页面，请重新播放或更换线路')
        handler.send_response(upstream.status)
        handler.send_header('Content-Type', mime)
        for name in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
            if upstream.headers.get(name): handler.send_header(name, upstream.headers[name])
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Connection', 'close')
        handler.end_headers()
        handler.close_connection = True
        try:
            block = prefix
            while block and not session['closed'].is_set():
                handler.wfile.write(block)
                if session['closed'].is_set(): break
                block = upstream.read1(64*1024)
        except (OSError, HTTPException):
            # Headers may already have been sent. Closing signals a truncated resource.
            return

def upstream_error(error):
    if isinstance(error, HTTPError):
        code = error.code
        error.close()
        return '上游返回 HTTP '+str(code)+('，请求过于频繁，请稍候重试' if code == 429 else '，可重试或更换线路')
    if isinstance(error, (TimeoutError, URLError)):
        return '连接直播源超时或失败，请重试'
    return str(error)

def record_error(query, error):
    with lock:
        session = sessions.get(query.get('session', [''])[0])
        if session:
            session['error'] = upstream_error(error)

def last_error(key):
    with lock:
        session = sessions.get(key)
        return session.get('error', '') if session else ''

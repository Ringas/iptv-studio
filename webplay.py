"""Short-lived browser playback sessions for catalogued routes."""
import hashlib
import json
import math
from email.utils import parsedate_to_datetime
from diagnostics import event, route_id
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

class UpstreamFailure(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__(details['error'])


def response_failure(body='', headers=None, stage='entry', http_status=None):
    """Interpret explicit error/cooldown metadata without guessing from arbitrary numbers."""
    headers = headers or {}
    def delay(value):
        if value is None or value == '': return 0
        try:
            n = float(value)
            if math.isfinite(n) and n >= 0: return math.ceil(n)
        except (ValueError, TypeError): pass
        try: return max(0, math.ceil(parsedate_to_datetime(str(value)).timestamp()-time.time()))
        except (ValueError, TypeError, OverflowError): return 0
    def valid(value):
        return int(value) if re.fullmatch(r'(?:[45]\d{2}|607)', str(value or '')) else None
    text = body.decode('utf-8-sig', errors='replace') if isinstance(body, bytes) else str(body)
    try: parsed = json.loads(text[:8192])
    except (ValueError, TypeError): parsed = None
    obj = parsed if isinstance(parsed, dict) else {}
    detail = obj.get('error') if isinstance(obj.get('error'), dict) else {}
    message = next((v for v in (obj.get('message'), obj.get('msg'), obj.get('error'), obj.get('desc'), obj.get('data'), detail.get('message')) if isinstance(v, str)), parsed if isinstance(parsed, str) else '' if isinstance(parsed, dict) else text)
    message = re.sub(r'https?://[^\s"<>]+', '[地址]', message, flags=re.I)
    code = valid(http_status) or valid(obj.get('httpStatus', obj.get('statusCode', obj.get('code', detail.get('code')))))
    codes = [int(v) for v in re.findall(r'\bHTTP\s*([45]\d{2}|607)\b', message, re.I)]
    code = code or (429 if 429 in codes else 607 if 607 in codes else codes[0] if codes else None)
    hints = re.findall(r'(?:暂停|冷却|等待|重试|停止探测)[^\d\n]{0,12}(\d+(?:\.\d+)?)\s*秒', message)
    hints += re.findall(r'(\d+(?:\.\d+)?)\s*秒(?:内)?(?:暂停|停止|后重试|后再试|后恢复)', message)
    retry = max(delay(headers.get('Retry-After',headers.get('retry-after'))), delay(obj.get('retryAfter', obj.get('retry_after', detail.get('retryAfter')))), 0, *(math.ceil(float(v))+(1 if code==429 else 0) for v in hints if math.isfinite(float(v))))
    policy = bool(re.search(r'network policy|policy blocked|blocked by policy|网络策略|策略拦截', message, re.I))
    if not code and not retry and not policy: return None
    message = re.sub(r'\b(token|sign|auth|key|password)\s*[=:]\s*[^&\s"<>]+', r'\1=[已隐藏]', message, flags=re.I)
    message = re.sub(r'[A-Za-z0-9%_=-]{28,}', '[标识]', message)
    message = re.sub(r'\s+', ' ', message).strip()[:300]
    return dict(httpStatus=code, retryAfter=retry or None, upstreamCooldown=retry>0,
                retryable=code!=401 and (retry>0 or policy or code in (408,425,429,500,502,503,504,607) or (code==403 and stage=='media')),
                stage=stage, error=message or f'上游返回 HTTP {code}')


def failure_details(error, stage='entry'):
    if isinstance(error, UpstreamFailure): return dict(error.details)
    cached = getattr(error, '_studio_details', None)
    if cached is not None: return dict(cached)
    if isinstance(error, HTTPError):
        try: body = error.read(8192)
        except OSError: body = b''
        info = response_failure(body, error.headers, stage, error.code)
        error.close()
        info = info or dict(error=f'上游返回 HTTP {error.code}',httpStatus=error.code,retryAfter=None,retryable=False,stage=stage)
        info['error'] = f"上游返回 HTTP {error.code}" + ('：'+info['error'] if info['error'] else '')
    elif isinstance(error, (TimeoutError, URLError)):
        info = dict(error='连接直播源超时或失败，请重试', httpStatus=None, retryAfter=None, retryable=True, stage=stage)
    else:
        info = dict(error=str(error), httpStatus=None, retryAfter=None, retryable=False, stage=stage)
    if info.get('httpStatus')==429: info['retryAfter']=max(6,info.get('retryAfter') or 0)
    error._studio_details = info
    return dict(info)


cooldowns = OrderedDict()

def remember_cooldown(url, info):
    seconds = info.get('retryAfter') or 0
    if not seconds or not info.get('retryable'): return
    with lock:
        host = urlsplit(url).netloc.lower()
        until, previous = cooldowns.get(host, (0, {}))
        if time.monotonic()+seconds >= until: cooldowns[host]=(time.monotonic()+seconds,dict(info))
        cooldowns.move_to_end(host)
        while len(cooldowns)>256: cooldowns.popitem(last=False)


def pending_cooldown(url):
    with lock:
        host = urlsplit(url).netloc.lower()
        until, info = cooldowns.get(host, (0, {}))
        left = math.ceil(until-time.monotonic())
        if left>0: return {**info,'retryAfter':left,'error':f'上游暂时拒绝请求，冷却约 {left} 秒后可重试'}
        cooldowns.pop(host,None)
    return None


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
            event('play.finished','本次播放已结束','INFO' if session.get('root') else 'DEBUG',channel=session.get('channel'),line=session.get('line_name'),play_id=route_id(key),route_id=route_id(session['root']) if session.get('root') else None,elapsed_ms=round((time.monotonic()-session['started'])*1000),bytes=session['bytes'],media_requests=session['media_requests'],media_completed=session['media_completed'],failures=session['failures'])

def new_session(line):
    waiting = pending_cooldown(line['url'])
    if waiting: raise UpstreamFailure(waiting)
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
                       closed=threading.Event(), touched=time.monotonic(),started=time.monotonic(),channel=line.get('channel',''),line_name=line.get('line_name',''),
                       bytes=0,media_requests=0,media_completed=0,failures=0,last_error_log=0)
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
                failure = response_failure(prefix+upstream.read(max(0,8192-len(prefix))),upstream.headers,'entry')
                if failure: raise UpstreamFailure(failure)
                raise ValueError('该线路返回的格式暂不支持网页播放，支持 HLS、MP4 和 WebM')
        session['root'] = line['url']
        if kind == 'native': address = resource(session, line['url'])
        event('play.started','开始网页播放',channel=session['channel'],line=session.get('line_name'),play_id=route_id(key),route_id=route_id(line['url']),format=kind)
        return dict(session=key, url=address, kind=kind)
    except Exception as error:
        info = failure_details(error,'entry')
        remember_cooldown(line['url'],info)
        info = failure_details(error, 'entry')
        event('play.failed','暂时无法播放这条线路','WARNING',channel=session['channel'],line=session.get('line_name'),route_id=route_id(line['url']),reason=upstream_error(error),error_type=type(error).__name__,http_status=info.get('httpStatus'),retry_after=info.get('retryAfter'),tip='可稍后重试，或切换其他线路；此错误不自动改变线路启停')
        close_session(key)
        raise UpstreamFailure(info) from None

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
    waiting = pending_cooldown(session['root'])
    if waiting: raise UpstreamFailure(waiting)
    headers = dict(session['manifest'] if manifest else session['media'])
    span = handler.headers.get('Range')
    if span:
        if not re.fullmatch(r'bytes=(?:\d+-\d*|-\d+)', span):
            handler.send(416, '无效字节范围', 'text/plain'); return
        headers['Range'] = span
    started=time.monotonic()
    if not manifest:
        with lock:session['media_requests']+=1
    with session['opener'].open(Request(address, headers=headers), timeout=12) as upstream:
        mime = upstream.headers.get_content_type()
        prefix = upstream.read(1024)
        if prefix.lstrip(b'\xef\xbb\xbf \r\n').startswith(b'#EXTM3U'):
            body = prefix+upstream.read(2*1024*1024)
            if len(body) > 2*1024*1024: raise ValueError('直播清单过大')
            handler.send(200, rewrite(session, body.decode('utf-8-sig'), upstream.url), 'application/vnd.apple.mpegurl')
            event('play.manifest','播放清单已刷新','DEBUG',channel=session['channel'],line=session.get('line_name'),play_id=route_id(key),elapsed_ms=round((time.monotonic()-started)*1000),segments=body.count(b'#EXTINF:'),http_status=upstream.status)
            return
        if mime=='text/html' or 'json' in mime:
            failure=response_failure(prefix+upstream.read(max(0,8192-len(prefix))),upstream.headers,'manifest' if manifest else 'media')
            if failure: raise UpstreamFailure(failure)
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
            sent=0
            block = prefix
            while block and not session['closed'].is_set():
                handler.wfile.write(block)
                with lock:session['bytes']+=len(block)
                sent+=len(block)
                if session['closed'].is_set(): break
                block = upstream.read1(64*1024)
            if not block:
                expected=upstream.headers.get('Content-Length')
                if expected is None or (expected.isdigit() and sent==int(expected)):
                    with lock:
                        session['media_completed']+=1
                        if started>=session.get('error_at',0):
                            session.pop('error',None);session.pop('error_details',None)
                else:record_error(query,ValueError('媒体传输提前结束，可重试或切换线路'))
        except (BrokenPipeError,ConnectionResetError):
            event('play.client_closed','播放器已断开本次媒体请求','DEBUG',channel=session['channel'],line=session.get('line_name'),play_id=route_id(key))
            return
        except (OSError, HTTPException) as error:
            # Headers were sent: do not append an error page to an incomplete segment.
            record_error(query,error)
            return

def upstream_error(error):
    return failure_details(error)['error']

def record_error(query, error):
    with lock:
        session = sessions.get(query.get('session', [''])[0])
        if session:
            item=session['resources'].get(query.get('resource',[''])[0])
            info=failure_details(error,'manifest' if item and item[1] else 'media')
            remember_cooldown(session['root'],info)
            session['error_details']=info
            session['error_at']=time.monotonic()
            session['error'] = info['error']
            session['failures']+=1
            if time.monotonic()-session['last_error_log']>=10 or session['failures']==1:
                session['last_error_log']=time.monotonic()
                event('play.upstream_error','播放传输暂时失败','WARNING',channel=session.get('channel'),line=session.get('line_name'),play_id=route_id(session['id']),route_id=route_id(session['root']),reason=session['error'],http_status=info.get('httpStatus'),retry_after=info.get('retryAfter'),failures=session['failures'],tip='可等待播放器重试，或切换其他线路；这不代表线路永久失效')

def last_error(key):
    with lock:
        session = sessions.get(key)
        return session.get('error', '') if session else ''


def last_status(key):
    with lock:
        session=sessions.get(key)
        if not session:return {'error':''}
        info=dict(session.get('error_details',{}))
        if info.get('retryAfter'):info['retryAfter']=max(0,math.ceil(info['retryAfter']-(time.monotonic()-session.get('error_at',time.monotonic()))))
        return {'error':session.get('error',''),**info}

"""Bounded, credential-safe operational events; no third-party logging dependency."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import functools
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# One runtime default for Python, child Node/FFmpeg processes and container logs.
os.environ.setdefault('TZ', 'Asia/Shanghai')
if hasattr(time, 'tzset'): time.tzset()

_context = ContextVar('diagnostic_context', default={})
_url = re.compile(r'https?://[^\s<>"\']+', re.I)
_secret = re.compile(r'(?i)\b(authorization|proxy-authorization|cookie|set-cookie|password|passwd|token|access_token|refresh_token|api[_-]?key|secret|session)\b(\s*[:=]\s*)(?:"[^"]*"|\'[^\']*\'|[^\s,;]+)')
_sensitive = re.compile(r'(?i)authorization|cookie|password|passwd|token|secret|api.?key|^session$|headers|options|payload')


def current_task():
    return _context.get().get('task_id')


def route_id(url):
    return hashlib.sha256(str(url).encode()).hexdigest()[:12]


def address(url):
    try:
        value = urlsplit(str(url))
        if value.path=='/…' and re.fullmatch(r'[0-9a-f]{12}',value.fragment or '') and not value.username and not value.query:return str(url)
        host = value.hostname or 'unknown'
        if ':' in host: host = '['+host+']'
        if value.port: host += ':'+str(value.port)
        if value.path in ('','/') and not value.query and not value.fragment and not value.username:return f'{value.scheme}://{host}'+value.path
        return f'{value.scheme}://{host}/…#{route_id(url)}'
    except ValueError:
        return '[invalid-address]'


def safe(value, depth=0):
    if depth > 5: return '[truncated]'
    if isinstance(value, dict):
        return {str(k)[:80]: '[redacted]' if _sensitive.search(str(k)) else safe(v, depth+1) for k, v in list(value.items())[:48]}
    if isinstance(value, (list, tuple)):
        return [safe(v, depth+1) for v in value[:24]]
    if isinstance(value,float) and not math.isfinite(value): return None
    if value is None or isinstance(value, (int, float, bool)): return value
    text = _url.sub(lambda m: address(m[0]), str(value))
    # Header values can contain spaces; suppress the entire header rather than a word.
    text = re.sub(r'(?im)\b(authorization|proxy-authorization|cookie|set-cookie)\s*[:=][^\r\n]*', r'\1=[redacted]', text)
    text = _secret.sub(lambda m: m[1]+m[2]+'[redacted]', text)
    return re.sub(r'[\x00-\x1f\x7f]', ' ', text)[:1600]


class EventFormatter(logging.Formatter):
    def format(self, record):
        try: timezone=ZoneInfo(os.getenv('TZ','Asia/Shanghai'))
        except ZoneInfoNotFoundError: timezone=ZoneInfo('Asia/Shanghai')
        data = {'time': datetime.now(timezone).isoformat(timespec='milliseconds'),
                'level': record.levelname, 'event': record.event, 'message': safe(record.getMessage()),
                **safe(record.fields)}
        if os.getenv('IPTV_LOG_FORMAT', 'text').lower() == 'json':
            return json.dumps(data, ensure_ascii=False, allow_nan=False)
        detail = record.fields
        prefix = ' · '.join(str(detail[key]) for key in ('channel', 'line', 'source') if detail.get(key))
        parts = [prefix] if prefix else []
        if detail.get('attempt'): parts.append('异常复测' if detail['attempt']=='recheck' else '首测')
        if detail.get('result'): parts.append(str(detail['result']))
        parts.append(data['message'].replace('测速','检测'))
        if detail.get('reason'): parts.append('原因：'+str(safe(detail['reason']))[:400])
        if detail.get('elapsed_ms') is not None: parts.append(f"耗时 {detail['elapsed_ms']/1000:.1f} 秒")
        if detail.get('wait_seconds') is not None: parts.append(f"等待 {detail['wait_seconds']:.0f} 秒")
        if detail.get('source_count') is not None: parts.append(f"共 {detail['source_count']} 个订阅")
        if detail.get('route_count') is not None: parts.append(f"共 {detail['route_count']} 条线路")
        if detail.get('bytes') is not None: parts.append(f"已传输 {detail['bytes']/1024/1024:.1f} MB")
        if detail.get('media_requests') is not None: parts.append(f"媒体请求 {detail['media_requests']} 次，完整传输 {detail.get('media_completed',0)} 次")
        if detail.get('tip'): parts.append(str(detail['tip']))
        if record.levelno==logging.DEBUG:
            parts.append(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
        level = {'DEBUG':'诊断','INFO':'信息','WARNING':'提醒','ERROR':'错误','CRITICAL':'严重错误'}.get(record.levelname,record.levelname)
        stamp=data['time'].replace('T',' ')
        stamp=stamp[:-6]+' '+stamp[-6:]
        return f'[{stamp}] [{level}] '+str(safe(' ｜ '.join(parts)))



logger = logging.getLogger('iptv-studio')
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(EventFormatter())
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG)


def event(name, message, level='INFO', **fields):
    threshold = getattr(logging, os.getenv('IPTV_LOG_LEVEL', 'INFO').upper(), logging.INFO)
    number = getattr(logging, level, logging.INFO)
    if number < threshold: return
    try:
        logger.log(number, safe(message), extra={'event': name, 'fields': safe({**_context.get(), **fields})})
    except Exception:
        # Diagnostics must never change publication, probing or database decisions.
        pass


@contextmanager
def bind(**fields):
    token = _context.set({**_context.get(), **fields})
    try: yield
    finally: _context.reset(token)


def exception(name, message, error, **fields):
    frames = traceback.extract_tb(error.__traceback__)[-8:]
    event(name, message, 'ERROR', error_type=type(error).__name__, reason=str(error),
          frames=[{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name} for f in frames], **fields)


def task_context(job):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(action, requested):
            started = time.monotonic()
            with bind(task_id=job.get('run_id'), action=action):
                label={'probe':'线路检测','refresh':'订阅更新','scheduled':'计划更新'}.get(action,'后台任务')
                event('task.started', '开始'+label+('（所选线路）' if requested is not None else ''), selected_count=None if requested is None else len(requested))
                try:
                    return function(action, requested)
                finally:
                    event('task.finished', job.get('message', '任务结束'),
                          'ERROR' if str(job.get('message', '')).startswith('任务中断') else 'INFO',
                          elapsed_ms=round((time.monotonic()-started)*1000), completed=job.get('done'), total_attempts=job.get('total'))
        return wrapped
    return decorate


def probe_details(result):
    evidence = result.get('evidence') or {}
    decoded = evidence.get('decode') or {}
    return dict(method=result.get('method'), error_category=result.get('error_category'), http_status=result.get('httpStatus'),
                speed_kbps=result.get('speed'), resolution=result.get('resolution'), startup_seconds=result.get('startup'),
                observed_seconds=result.get('play_seconds', result.get('seconds')), segments=result.get('segments'),
                stalls=result.get('stalls'), stall_seconds=result.get('stall_seconds'), entry_retries=result.get('entry_retries', 0),
                evidence={key: value.get('state') for key, value in evidence.items() if isinstance(value, dict)},
                gateway_sessions=result.get('gateway_sessions'), handoff=result.get('handoff'), recovery=result.get('recovery'), recovery_check=result.get('recovery_check'),
                process_error=result.get('process_error'), decode_exit_code=decoded.get('exit_code'), decode_error=decoded.get('stderr_tail'), decoded_frames=decoded.get('frames'), pts_advanced=decoded.get('pts_advanced'), notes=result.get('note', []))

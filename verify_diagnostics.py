"""Operational logs stay readable, bounded, credential-safe, and observational."""
import io
import json
import logging
import re
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
from unittest.mock import patch
import diagnostics as d

stream=io.StringIO()
handler=logging.StreamHandler(stream)
handler.setFormatter(d.EventFormatter())
old=d.logger.handlers[:]
d.logger.handlers=[handler]
try:
    with patch.dict(os.environ,{'IPTV_LOG_LEVEL':'INFO','IPTV_LOG_FORMAT':'text','TZ':'Asia/Shanghai'}):
        with d.bind(task_id='a1'):
            d.event('probe.phase','内部读取中','DEBUG',phase='fetch')
            d.event('record.probe','流畅 · 2.50 MB/s · 1920 × 1080',channel='演示综合',source='示例订阅',result='通过',attempt='initial',url='https://u:p@example.invalid/private-token/stream?token=query-secret',diagnostic={'Authorization':'Basic basic-secret'})
        text=stream.getvalue()
        assert '[信息]' in text and '演示综合' in text and '首测' in text and '2.50 MB/s' in text
        assert 'probe.phase' not in text and 'task_id' not in text and 'diagnostic' not in text
        assert all(secret not in text for secret in ['query-secret','private-token','basic-secret','u:p'])
        assert len(text.splitlines())==1
        assert re.match(r'\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \+08:00\]',text)
    stream.seek(0);stream.truncate()
    with patch.dict(os.environ,{'IPTV_LOG_FORMAT':'json','IPTV_LOG_LEVEL':'DEBUG','TZ':'Asia/Shanghai'}):
        def worker(value):
            with d.bind(task_id=value):
                d.event('test.secret','failed https://user:pass@example.invalid/secret-path?auth=private-query\nAuthorization: Bearer private-header',url='https://user:pass@example.invalid/secret-path?auth=private-query',headers={'Cookie':'private-cookie'},notes=['password=private-password'],phase='decode',value=float('nan'))
        threads=[threading.Thread(target=worker,args=(key,)) for key in ['one','two']]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        d.event('test.none','outside task')
        text=stream.getvalue();rows=[json.loads(line) for line in text.splitlines()]
        assert {row['task_id'] for row in rows[:2]}=={'one','two'} and 'task_id' not in rows[-1]
        assert all(word not in text for word in ['private-query','secret-path','private-header','private-cookie','private-password','user:pass'])
        assert rows[0]['time'].endswith('+08:00')
        assert abs((datetime.fromisoformat(rows[0]['time'])-datetime.now(timezone.utc)).total_seconds())<5
        assert rows[0]['phase']=='decode' and rows[0]['value'] is None
        assert d.route_id('https://a.invalid/1')!=d.route_id('https://a.invalid/2')
        assert d.safe('https://example.invalid/…#abcdefabcdef')=='https://example.invalid/…#abcdefabcdef'
        stream.seek(0);stream.truncate()
        try:
            raise RuntimeError('token=hidden-value https://u:p@host.invalid/hidden-path')
        except RuntimeError as error:d.exception('test.error','内部错误',error)
        row=json.loads(stream.getvalue());assert row['error_type']=='RuntimeError' and row['frames'][-1]['file']=='verify_diagnostics.py'
        assert 'hidden-value' not in stream.getvalue() and 'hidden-path' not in stream.getvalue()
    env={key:value for key,value in os.environ.items() if key!='TZ'}
    output=subprocess.check_output([sys.executable,'-c',"import diagnostics,os;print(os.environ['TZ'])"],env=env,text=True)
    assert output.strip()=='Asia/Shanghai'
    # A surrounding tool's generic LOG_FORMAT must not turn user logs into JSON.
    env={key:value for key,value in os.environ.items() if key not in ('IPTV_LOG_FORMAT','IPTV_LOG_LEVEL')}
    env['LOG_FORMAT']='json'
    output=subprocess.check_output([sys.executable,'-c',"import diagnostics;diagnostics.event('default','简短中文提示')"],env=env,text=True)
    assert '[信息]' in output and not output.startswith('{')
    import service
    with tempfile.TemporaryDirectory() as temp, patch.object(service,'DATA',Path(temp)),patch.dict(os.environ,{'IPTV_LOG_LEVEL':'ERROR'}):
        service.init()
        with service.db() as c:
            service.log(c,'演示日志','probe',result='未能验证',reason='临时超时',url='https://example.invalid/secret')
            row=c.execute('select message,details from logs').fetchone()
            assert row[0]=='演示日志' and json.loads(row[1])['reason']=='临时超时'
    # Repeated scheduler faults are bounded; a successful iteration reports recovery.
    with tempfile.TemporaryDirectory() as temp, patch.object(service,'DATA',Path(temp)):
        service.init();real_db=service.db;calls=[0]
        def intermittent_db():
            calls[0]+=1
            if calls[0]<=2:raise PermissionError('data unavailable')
            return real_db()
        stream.seek(0);stream.truncate()
        with patch.object(service,'db',side_effect=intermittent_db),patch.object(service.time,'sleep',side_effect=[None,None,None,StopIteration]),patch.dict(os.environ,{'IPTV_LOG_LEVEL':'INFO','IPTV_LOG_FORMAT':'json'}):
            try:service.scheduler()
            except StopIteration:pass
        rows=[json.loads(line) for line in stream.getvalue().splitlines()]
        assert [r['event'] for r in rows]==['scheduler.error','scheduler.recovered'],rows
    print('PASS: readable defaults, Shanghai timestamps, thread context, redaction, exceptions, scheduler recovery and database compatibility')
finally:
    d.logger.handlers=old

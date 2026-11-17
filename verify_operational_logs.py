"""Exercise real HTTP playback/polling, server failures and child-process diagnostics."""
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler,Request
from unittest.mock import patch
import diagnostics as d
import service
import app

stream=io.StringIO();handler=logging.StreamHandler(stream);handler.setFormatter(d.EventFormatter())
class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def do_GET(self):
        body=b'#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5,\n/part.ts?token=media-private\n' if self.path.startswith('/list') else b'fixture media'
        self.send_response(200);self.send_header('Content-Type','application/vnd.apple.mpegurl' if self.path.startswith('/list') else 'video/mp2t');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)

def main():
    old=d.logger.handlers[:];d.logger.handlers=[handler]
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    for instance in [upstream,server]:threading.Thread(target=instance.serve_forever,daemon=True).start()
    opener=build_opener(ProxyHandler({}));base=f'http://127.0.0.1:{server.server_port}'
    def request(path,data=None):
        req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=5) as response:return response.read()
    try:
      with tempfile.TemporaryDirectory() as temp,patch.object(service,'DATA',Path(temp)),patch.object(app,'PASSWORD',''),patch.dict(os.environ,{'IPTV_LOG_LEVEL':'INFO','IPTV_LOG_FORMAT':'text'}):
        service.init()
        url=f'http://127.0.0.1:{upstream.server_port}/list.m3u8?token=source-private'
        with service.db() as c:c.execute('insert into sources(name,kind,url,enabled,priority,items) values(?,?,?,?,?,?)',('演示订阅','playlist','https://example.invalid/sub-private',1,1,json.dumps([{'name':'演示综合','url':url,'attrs':{},'options':[]}])) )
        for _ in range(3):request('/api/status?compact=1');request('/health');request('/api/state')
        assert stream.getvalue()=='',stream.getvalue()
        play=json.loads(request('/api/play',{'name':'演示综合','url':url}))
        manifest=request(play['url']).decode();media=next(line for line in manifest.splitlines() if line and not line.startswith('#'))
        assert request(media)==b'fixture media'
        request('/api/close-play',{'session':play['session']})
        text=stream.getvalue()
        assert len(text.splitlines())==2 and '开始网页播放' in text and '本次播放已结束' in text
        assert '完整传输 1 次' in text
        assert all(secret not in text for secret in ['source-private','media-private',play['session'],'session='])
        with patch.object(app,'edit',side_effect=RuntimeError('password=hidden-admin')):
            try:request('/api/settings',{})
            except HTTPError as error:assert error.code==500 and '服务内部错误' in error.read().decode()
            else:raise AssertionError('Expected internal error')
        assert 'hidden-admin' not in stream.getvalue()
        script=Path(temp)/'probe.mjs';script.write_text("process.stderr.write('Authorization: Bearer child-secret\\ntoken=child-token\\n');process.exit(7)")
        stream.seek(0);stream.truncate()
        with patch.object(service,'ROOT',Path(temp)),patch.dict(os.environ,{'IPTV_LOG_LEVEL':'DEBUG','IPTV_LOG_FORMAT':'json'}):
            service.job.update(stop_line=False,stop_queue=False,rechecking=False)
            result=service.probe(url,'quick',[])
            assert result['process_error']['exit_code']==7 and result['quality']=='暂时失败'
            rows=[json.loads(line) for line in stream.getvalue().splitlines()]
            failed=next(row for row in rows if row['event']=='probe.process_error')
            assert failed['exit_code']==7 and not failed['timed_out']
            assert all(secret not in stream.getvalue() for secret in ['child-secret','child-token','source-private'])
        print('PASS: quiet polling, readable playback summary, masked HTTP failure and retained child stderr diagnostics')
    finally:
        for instance in [server,upstream]:instance.shutdown();instance.server_close()
        d.logger.handlers=old

if __name__=='__main__':main()

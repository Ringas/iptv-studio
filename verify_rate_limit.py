"""Rate-limit regressions. All requests go to local fixtures."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import service


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(service.gateway_windows.clear)
        service.gateway_windows.clear()

    def test_short_burst(self):
        with patch.object(service, 'gateway_clock', return_value=100) as clock:
            self.assertEqual(service.gateway_delay('http://gateway/1', True), 0)
            self.assertEqual(service.gateway_delay('http://gateway/1'), 2.5)
            self.assertEqual(service.gateway_delay('http://gateway/2'), 2.5)
            self.assertEqual(service.gateway_delay('http://other/1'), 0)
            clock.return_value = 102.5
            self.assertEqual(service.gateway_delay('http://gateway/2', True), 0)
            clock.return_value = 105
            self.assertEqual(service.gateway_delay('http://gateway/3', True), 0)
            self.assertEqual(service.gateway_delay('http://gateway/4'), 6)
            clock.return_value = 111
            self.assertEqual(service.gateway_delay('http://gateway/4', True), 0)

    def test_scheduler(self):
        requests=[]
        class Gateway(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append((self.path,time.monotonic()))
                if self.path=='/master':
                    body=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000\n/hi\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\n/lower\n'
                elif self.path=='/hi':
                    body=b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\n/limited.ts\n#EXTINF:4,\n/limited.ts\n'
                elif self.path=='/other' or self.path=='/a' and sum(p=='/a' for p,_ in requests)>1:
                    body=b'#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\n/first.ts\n#EXTINF:4,\n/last.ts\n'
                elif self.path=='/last.ts':
                    time.sleep(.035)
                    body=b'\x47'*65536
                else:
                    self.send_response(429)
                    self.send_header('Retry-After','60' if self.path=='/long' else '1')
                    self.end_headers()
                    self.wfile.write('央视频：停止探测约 5 秒后自动恢复'.encode())
                    return
                self.send_response(200);self.end_headers()
                try:self.wfile.write(body)
                except OSError:pass
        with ThreadingHTTPServer(('127.0.0.1',0),Gateway) as server:
            threading.Thread(target=server.serve_forever,daemon=True).start()
            port=server.server_port
            gateway=f'http://127.0.0.1:{port}'
            a,other,long_url= gateway+'/a',f'http://localhost:{port}/other',gateway+'/long'
            with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
                service.init()
                with service.db() as c:
                    cfg={**service.config(c),'probe_mode':'legacy'}
                    items=[{'url':u,'name':str(i),'attrs':{},'options':[]} for i,u in enumerate([a,other,long_url])]
                    c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(json.dumps(items),))
                    prior=service.encode({'status':'保留','speed':1000,'direct_history':[1]})
                    c.execute('insert into tests values(?,?)',(long_url,prior))
                    c.execute('insert into route_modes values(?,?,?)',('2',long_url,'off'))
                service.job.update(stop_queue=False)
                with patch.object(service,'config',return_value=cfg):
                    service.run('probe',[a,other])
                    service.run('probe',[long_url])
                    service.run('probe',[long_url])
                entries=[(p,t) for p,t in requests if p!='/last.ts']
                self.assertEqual([p for p,_ in entries],['/a','/other','/a','/long'])
                self.assertGreaterEqual(entries[2][1]-entries[0][1],6)
                with service.db() as c:
                    result=json.loads(c.execute('select result from tests where url=?',(a,)).fetchone()[0])
                    self.assertGreater(result['speed'],100)
                    self.assertIn('429',str(result['note']))
                    self.assertEqual(c.execute('select result from tests where url=?',(long_url,)).fetchone()[0],prior)
                    self.assertEqual(c.execute('select mode from route_modes where url=?',(long_url,)).fetchone()[0],'off')
                self.assertIn('待确认',service.job['message'])
            service.job['stop_line']=False
            with patch.dict(service.job,sample=None):
                result=service.probe(gateway+'/master','quick',[])
            self.assertEqual(result['httpStatus'],429)
            self.assertGreaterEqual(result['retryAfter'],6)
            self.assertEqual([p for p,_ in requests[-3:]],['/master','/hi','/limited.ts'])
            server.shutdown()


if __name__=='__main__':unittest.main()

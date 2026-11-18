"""Actual HTTP playback API: structured errors, no early requests and successful recovery."""
import json,threading,tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError
from urllib.parse import urljoin
from unittest.mock import patch
import service,webplay
from app import Handler
class Fixture(BaseHTTPRequestHandler):
 mode='wrapped';calls=0
 def log_message(self,*args):pass
 def do_GET(self):
  Fixture.calls+=1
  code=200;headers={}
  if self.path=='/entry' and self.mode=='wrapped':
   body=b'HTTP 403, token=secret-value; '+ '30 秒内暂停向官方请求'.encode();mime='application/json'
  elif self.path=='/entry' and self.mode=='auth':code=403;body=b'login required';mime='text/plain'
  elif self.path=='/entry':body=b'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nmedia.ts\n';mime='application/vnd.apple.mpegurl'
  elif self.mode=='media-error':code=403;body=b'expired signature';mime='text/plain';headers['Retry-After']='9'
  else:body=(b'G'+b'x'*187)*64;mime='video/mp2t'
  self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
  for k,v in headers.items():self.send_header(k,v)
  self.end_headers();self.wfile.write(body)
with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
 service.init();media=ThreadingHTTPServer(('127.0.0.1',0),Fixture);api=ThreadingHTTPServer(('127.0.0.1',0),Handler)
 for server in (media,api):threading.Thread(target=server.serve_forever,daemon=True).start()
 base=f'http://127.0.0.1:{api.server_port}';url=f'http://127.0.0.1:{media.server_port}/entry';op=build_opener(ProxyHandler({}))
 with service.db() as c:c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name='CCTV1',url=url,attrs={},options=[])]),))
 def request(path,data=None):
  req=Request(urljoin(base,path),data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
  try:
   with op.open(req,timeout=5) as r:return r.status,r.headers,r.read()
  except HTTPError as e:return e.code,e.headers,e.read()
 def start():return request('/api/play',{'name':'CCTV1综合','url':url})
 try:
  webplay.cooldowns.clear()
  code,headers,body=start();data=json.loads(body);assert code==502 and 'application/json' in headers['Content-Type'],(code,body)
  assert data['httpStatus']==403 and data['retryAfter']==30 and data['retryable'],data
  assert b'secret-value' not in body and '格式' not in data['error']
  count=Fixture.calls;again=json.loads(start()[2]);assert Fixture.calls==count and 1<=again['retryAfter']<=30
  webplay.cooldowns.clear();Fixture.mode='auth'
  data=json.loads(start()[2]);assert data['httpStatus']==403 and data['retryable'] is False,data
  Fixture.mode='media-error';code,_,body=start();assert code==200,body
  session=json.loads(body);manifest=request(session['url'])[2].decode();segment=next(line for line in manifest.splitlines() if line and not line.startswith('#'))
  code,headers,body=request(segment);info=json.loads(body);assert code==502 and info['httpStatus']==403 and info['stage']=='media' and info['retryAfter']==9,info
  status=json.loads(request('/api/play-status?session='+session['session'])[2]);assert status['httpStatus']==403 and 1<=status['retryAfter']<=9,status
  count=Fixture.calls;request(segment);assert Fixture.calls==count
  webplay.cooldowns.clear();Fixture.mode='good';code,_,body=request(segment);assert code==200 and body.startswith(b'G')
  status=json.loads(request('/api/play-status?session='+session['session'])[2]);assert status['error']=='',status
  request('/api/close-play',{'session':session['session']});assert session['session'] not in webplay.sessions
  # No playback operation writes a measurement or changes route policy.
  with service.db() as c:assert c.execute('select count(*) from tests').fetchone()[0]==0 and c.execute('select count(*) from route_modes').fetchone()[0]==0
 finally:
  for key in list(webplay.sessions):webplay.close_session(key)
  webplay.cooldowns.clear()
  for server in (media,api):server.shutdown();server.server_close()
print('PASS: actual HTTP error metadata, redaction, cooldown suppression, ordinary auth refusal, cleared stale errors and unchanged measurements/activation.')

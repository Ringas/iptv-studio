"""同地址台标变化后，状态版本、UI 地址及导出同步更新；无变化不刷新版本。"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import build_opener,ProxyHandler
import tempfile,threading,json,time
import app,service,logos
class Fixture(BaseHTTPRequestHandler):
 color=30;hits=0
 def log_message(self,*_):pass
 def do_GET(self):
  type(self).hits+=1;body=logos.png(20,12,bytes((self.color,60,140,255))*240)
  self.send_response(200);self.end_headers();self.wfile.write(body)
with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)),patch.object(app,'DATA',Path(d)):
 service.init();media=ThreadingHTTPServer(('127.0.0.1',0),Fixture);api=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
 for server in (media,api):threading.Thread(target=server.serve_forever,daemon=True).start()
 source=f'http://127.0.0.1:{media.server_port}/image';base=f'http://127.0.0.1:{api.server_port}';op=build_opener(ProxyHandler({}));folder=Path(d)/'logo-cache'
 def get(path):
  with op.open(base+path,timeout=20) as r:return r.read()
 try:
  with service.db() as c:
   item=dict(name='CCTV1',url='https://fixture.invalid/live',attrs={'tvg-logo':source},options=[])
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([item]),))
   cfg=service.config(c);cfg.update(logo_beauty='dark',minutes=0,strict=False);service.save_config(c,cfg)
  state=json.loads(get('/api/state'));route=state['channels'][0]['logo'];first=get(route)
  before=json.loads(get('/api/state'));old=before['channels'][0]['logo'];revision=before['logo_cache_revision'];assert '?v=' in old
  assert get(old)==first and Fixture.hits==1
  assert json.loads(get('/api/status?compact=1'))['logo_cache_revision']==revision
  Fixture.color=160
  with logos._index_lock:
   index=logos._index(folder);index['sources'][logos._url_key(source)]['checked']=time.time()-logos.CHECK_INTERVAL-1;logos._save_index(folder,index)
  assert get(old)!=first
  state=json.loads(get('/api/state'));new=state['channels'][0]['logo']
  assert new!=old and state['logo_cache_revision']>revision
  assert json.loads(get('/api/status?compact=1'))['logo_cache_revision']==state['logo_cache_revision']
  assert (base+new).encode() in get('/live.m3u') and (base+new).encode() in get('/guide.xml')
 finally:media.shutdown();api.shutdown()
print('通过：同地址内容变更刷新图片版本，未变化不刷新，状态轮询可通知前台，M3U/EPG 与显示地址一致。')

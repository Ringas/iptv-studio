"""台标订阅、优先级、失败兜底、美化与发布一致性；不使用真实数据。"""
import base64
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError
from unittest.mock import patch
import app,service,logos
from catalog import logo_keys

# Transparent fixture includes generous padding and light lettering.
w,h=80,60
pixels=bytearray(w*h*4)
for y in range(24,36):
 for x in range(15,65):pixels[(y*w+x)*4:(y*w+x+1)*4]=bytes((255,255,255,255))
image=logos.png(w,h,pixels)
for mode in logos.MODES:
 out=logos.render(image,mode);width,height=struct.unpack('>II',out[16:24]);raw=logos.ffmpeg(out,raw=True)
 assert (width,height)==((80,60) if mode=='off' else (300,180))
 if mode=='transparent':
  assert raw[:4]==b'\0\0\0\0'
  assert raw[(90*300+150)*4:(90*300+150)*4+4]==b'\xff\xff\xff\xff'
 elif mode!='off':
  assert tuple(raw[:4])==((237,241,246,255) if mode=='light' else (37,42,50,255))
  assert raw[(90*300+150)*4:(90*300+150)*4+3]==b'\xff\xff\xff'
# Transparent output preserves source alpha and embedded solid backgrounds.
for rgba in ((20,30,40,255),(120,130,140,128)):
 out=logos.ffmpeg(logos.render(logos.png(8,8,bytes(rgba)*64),'transparent'),raw=True)
 assert out[:4]==b'\0\0\0\0'
 center=tuple(out[(90*300+150)*4:(90*300+150)*4+4])
 assert all(abs(a-b)<=1 for a,b in zip(center,rgba)),(center,rgba)
try:logos.render(logos.png(1,1,b'\0\0\0\0'),'light');raise AssertionError('blank accepted')
except ValueError:pass
index=logos.parse_index(json.dumps({'CCTV-1':'../cctv.png','empty':'javascript:alert(1)'}).encode(),'https://fixture.invalid/folder/index.json')
assert index['name:CCTV1综合']=='https://fixture.invalid/cctv.png'
assert 'name:EMPTY' not in index
assert logos.request_url('https://fixture.invalid/江苏卫视.png?name=中文').startswith('https://fixture.invalid/%E6%B1%9F')
rows=logos.parse_index(b'[{"name":"CGTN","id":"cn.cgtn","logo":"/cgtn.png"}]','https://fixture.invalid/index')
assert rows['id:cn.cgtn']==rows['name:CGTN']
for bad in (b'{}',b'[]',b'null',b'not json'):
 try:logos.parse_index(bad,'https://fixture.invalid/');raise AssertionError('invalid snapshot accepted')
 except ValueError:pass

class Fixture(BaseHTTPRequestHandler):
 calls=[];broken_index=False
 def log_message(self,*_):pass
 def do_GET(self):
  self.calls.append(self.path)
  if self.path=='/missing.png':self.send_response(404);self.end_headers();return
  if self.path=='/index.json':
   body=b'{}' if self.broken_index else json.dumps({'CCTV1':base+'/missing.png','CGTN':base+'/override.png'}).encode();mime='application/json'
  else:body=image;mime='image/png'
  self.send_response(200);self.send_header('Content-Type',mime);self.end_headers();self.wfile.write(body)

with tempfile.TemporaryDirectory() as d:
 root=Path(d)
 with patch.object(service,'DATA',root),patch.object(app,'DATA',root):
  service.init();media=ThreadingHTTPServer(('127.0.0.1',0),Fixture);api=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
  for s in (media,api):threading.Thread(target=s.serve_forever,daemon=True).start()
  base=f'http://127.0.0.1:{media.server_port}';url=f'http://127.0.0.1:{api.server_port}';op=build_opener(ProxyHandler({}))
  try:
   items=[dict(name=n,url=base+'/live/'+str(i),attrs={'tvg-logo':base+'/fallback.png'},options=[]) for i,n in enumerate(['CCTV1','CGTN','江苏卫视','东方卫视'])]
   with service.db() as c:
    c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode(items),))
    cfg=service.config(c);assert cfg['logo_beauty']=='off';cfg['strict']=False;service.save_config(c,cfg)
    c.execute("insert into route_modes values('CCTV1综合',?,'on')",(items[0]['url'],))
   service.edit('source',dict(name='台标 A',kind='logo',url=base+'/index.json',priority=1))
   with service.db() as c:source=dict(c.execute("select * from sources where kind='logo'").fetchone())
   assert service.refresh(source,{})==(set(),[])
   with service.db() as c:
    channels=service.catalog(c);assert len(channels)==4
    first=channels[0];assert first['original_logo_candidates']==[base+'/missing.png',base+'/fallback.png']
    assert channels[1]['original_logo_candidates'][0]==base+'/override.png'
    assert channels[2]['original_logo_candidates']==[base+'/fallback.png']
    frozen_modes=list(map(tuple,c.execute('select * from route_modes')))
   def get(path):
    with op.open(url+path,timeout=30) as r:return r.read()
   content=get(first['logo']);assert content.startswith(b'\x89PNG');assert Fixture.calls[-2:]==['/missing.png','/fallback.png']
   count=len(Fixture.calls);assert get(first['logo'])==content and len(Fixture.calls)==count
   # Manual override wins even when a logo subscription exists.
   with service.db() as c:
    c.execute('insert into rules values(?,?)',('CGTN',service.encode({'logo':base+'/manual.png'})))
    assert service.catalog(c)[1]['original_logo_candidates']==[base+'/manual.png']
   service.edit('channel-logo',{'name':'CGTN','image':base64.b64encode(image).decode()})
   for mode in ['light','dark','transparent','off']:
    with service.db() as c:cfg=service.config(c)
    with patch.object(service,'probe',side_effect=AssertionError('saving style triggered probe')):
     service.edit('settings',{**cfg,'hours':0,'logo_beauty':mode})
    worker=service._logo_warm_thread
    if worker:worker.join(30);assert not worker.is_alive(),'background preparation did not finish'
    if mode!='off':
     with service.db() as c:prepared=service.catalog(c)
     assert all(logos.cache_path(ch['original_logo_candidates'],mode,root/'logo-cache').exists() for ch in prepared),'saving did not prepare unseen channels'
    assert not service.job['running'],'logo preparation entered the detection task queue'
    status=json.loads(get('/api/status?compact=1'));assert status['logo_beauty']==mode
    assert status['logo_optimization']['mode']==mode and not status['logo_optimization']['running']
    state=json.loads(get('/api/state'));route=state['channels'][0]['logo'];assert route.split('?')[0].endswith('/'+mode+'.png')
    out=get(route);size=struct.unpack('>II',out[16:24]);assert size==((80,60) if mode=='off' else (300,180))
    manual=get(state['channels'][1]['logo']);assert manual.startswith(b'\x89PNG')
    assert (url+route).encode() in get('/live.m3u')
    assert (url+route).encode() in get('/guide.xml')
    exported=json.loads(get('/live.json'));assert exported[0]['logo']==url+route
    with service.db() as c:assert list(map(tuple,c.execute('select * from route_modes')))==frozen_modes
   # An invalid snapshot must not erase the last good logo mapping.
   with service.db() as c:before=c.execute('select logos from sources where id=?',(source['id'],)).fetchone()[0]
   Fixture.broken_index=True
   try:service.refresh(source,{});raise AssertionError('empty feed accepted')
   except ValueError:pass
   with service.db() as c:assert c.execute('select logos from sources where id=?',(source['id'],)).fetchone()[0]==before
   # A logo-only scheduled update must never create playback/detection targets.
   Fixture.broken_index=False
   with service.db() as c:c.execute('update sources set next_update=1 where id=?',(source['id'],))
   service.job.update(stop_queue=False,stop_line=False)
   with patch.object(service,'probe',side_effect=AssertionError('logo feed triggered probe')):service.run('scheduled',None)
   service.edit('source-state',{'id':source['id'],'enabled':False})
   with service.db() as c:assert service.catalog(c)[0]['logo']==base+'/fallback.png'
   service.edit('source-state',{'id':source['id'],'enabled':True})
   service.edit('delete',{'id':source['id']})
   with service.db() as c:assert service.catalog(c)[0]['logo']==base+'/fallback.png'
   try:service.edit('settings',{**cfg,'hours':0,'logo_beauty':'invalid'});raise AssertionError('invalid mode accepted')
   except ValueError:pass
  finally:
   worker=service._logo_warm_thread
   if worker:worker.join(30)
   media.shutdown();api.shutdown();logos._failed.clear()
print('通过：JSON 台标匹配、手动优先、缺失/失效兜底、缓存、四种优化模式、M3U/JSON/EPG 一致、旧快照与手动启停保留。')

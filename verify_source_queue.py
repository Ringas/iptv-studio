"""任务中订阅编辑、持久化合并队列和旧结果写入保护；隔离 HTTP/数据库。"""
import copy
import json
import sqlite3
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.request import Request,build_opener,ProxyHandler
import app,service
from verify_dual_schedule import GOOD

# Old schemas migrate without nested transactions or changing existing sources.
with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
 with sqlite3.connect(Path(d)/'hub.sqlite') as c:
  c.execute("create table sources(id integer primary key,name text,kind text,url text,priority integer,enabled integer,items text default '[]',xml blob,last text default '',updated integer default 0)")
  c.execute("insert into sources(name,kind,url,priority,enabled) values('old','playlist','https://example.invalid/feed',1,1)")
 service.init()
 with service.db() as c:version=c.execute('select version from sources').fetchone()[0]
 service.init()
 with service.db() as c:
  assert version and c.execute('select version from sources').fetchone()[0]==version
  assert not c.execute('select * from source_refresh_queue').fetchall()

# In-flight guide/logo downloads must not revive obsolete metadata snapshots.
with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)),patch.object(service,'request_logo_warm'):
 service.init()
 for kind in ('logo','epg'):
  sid=service.edit('source',dict(name=kind,kind=kind,url=f'https://example.invalid/{kind}',priority=1,interval_hours=0))
  def row():
   with service.db() as c:return dict(c.execute('select * from sources where id=?',(sid,)).fetchone())
  def payload(text):
   return json.dumps({'CCTV1':'https://example.invalid/'+text+'.png'}).encode() if kind=='logo' else f'<tv><programme channel="x" start="20261118000000 +0800"><title>{text}</title></programme></tv>'.encode()
  with patch.object(service,'fetch',return_value=(payload('prior'),row()['url'])):service.refresh(row(),{})
  old=row()
  def changed(url,*args):
   service.edit('source',dict(id=sid,name=kind,kind=kind,url=f'https://example.invalid/new-{kind}',priority=1,interval_hours=0))
   return payload('stale'),url
  with patch.object(service,'fetch',side_effect=changed):
   try:service.refresh(old,{});raise AssertionError('stale update committed')
   except service.StaleSource:pass
  if kind=='logo':assert row()['logos']==old['logos']
  else:
   with service.db() as c:
    cached=c.execute('select xml from epg_cache where url=?',(row()['url'],)).fetchone()[0]
    assert b'prior' in cached and b'stale' not in cached

class Fixture(BaseHTTPRequestHandler):
 hits=Counter();entered=threading.Event();release=threading.Event()
 def log_message(self,*args):pass
 def do_GET(self):
  self.hits[self.path]+=1
  if self.path=='/hold':self.entered.set();assert self.release.wait(8)
  if self.path=='/logo':body=b'{"CCTV1":"https://image.example.invalid/logo.png"}'
  else:body=f'#EXTM3U\n#EXTINF:-1,CCTV1\nhttps://stream.example.invalid{self.path}\n'.encode()
  self.send_response(200);self.end_headers();self.wfile.write(body)

with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)),patch.object(app,'DATA',Path(d)),patch.object(service,'request_logo_warm'),patch.object(service,'collect_eit',return_value=[]):
 service.init();service.job.update(running=False,stop_queue=False)
 with service.db() as c:
  cfg=service.config(c);cfg.update(minutes=0,probe_mode='adaptive',strict=True);service.save_config(c,cfg)
 servers=[ThreadingHTTPServer(('127.0.0.1',0),h) for h in (Fixture,app.Handler)]
 for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
 origin=f'http://127.0.0.1:{servers[0].server_port}';base=f'http://127.0.0.1:{servers[1].server_port}'
 op=build_opener(ProxyHandler({}))
 def api(path,payload=None):
  request=Request(base+'/api/'+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
  with op.open(request,timeout=5) as response:return json.load(response)
 def save(path,**extra):return api('source',{**dict(name=path,kind='playlist',url=origin+path,priority=10,interval_hours=0),**extra})
 def wait():
  deadline=time.monotonic()+10
  while service.job['running'] and time.monotonic()<deadline:time.sleep(.01)
  assert not service.job['running'],service.job
  assert not service.job['message'].startswith('任务中断'),service.job
 def row(sid):
  with service.db() as c:return dict(c.execute('select * from sources where id=?',(sid,)).fetchone())
 try:
  # Busy tasks do not block saving; repeated edits are one durable queue entry.
  service.job.update(running=True,probing=True,action='probe')
  first=save('/first');sid=first['source_id'];assert first['refresh_queued']
  save('/second',id=sid);save('/final',id=sid)
  disabled=save('/disabled')['source_id'];deleted=save('/deleted')['source_id']
  api('source-state',dict(id=disabled,enabled=False));api('delete',dict(id=deleted))
  assert not Fixture.hits
  with service.db() as c:assert [r[0] for r in c.execute('select source_id from source_refresh_queue')]==[sid]
  service.init() # Simulate reopening persisted queue metadata after restart.
  assert next(s for s in api('state')['sources'] if s['id']==sid)['pending_update']
  service.job['running']=False;service.drain_refresh_queue();wait()
  assert Fixture.hits==Counter({'/final':1}) and '/final' in row(sid)['items']
  # Saved edits during a scheduled refresh cannot make its old URLs eligible.
  scheduled=save('/scheduled-a');wait();other=save('/scheduled-b');wait()
  scheduled_id=scheduled['source_id'];scheduled_url=json.loads(row(scheduled_id)['items'])[0]['url']
  with service.db() as c:
   c.execute('update sources set next_update=0')
   c.execute('update sources set next_update=1 where id in (?,?)',(scheduled_id,other['source_id']))
   c.execute('insert or replace into route_modes values(?,?,?)',('CCTV1综合',scheduled_url,'off'))
  def scheduled_refresh(source,*args):
   if source['id']==other['source_id']:
    service.edit('source',dict(id=scheduled_id,name='changed',kind='playlist',url=origin+'/scheduled-new',priority=10,interval_hours=0),queue_refresh=True)
   return ({scheduled_url} if source['id']==scheduled_id else set()),[]
  with patch.object(service,'refresh',side_effect=scheduled_refresh),patch.object(service,'probe',side_effect=AssertionError('stale scheduled URL probed')):
   service.run('scheduled',None);wait()
  with service.db() as c:assert c.execute('select mode from route_modes where url=?',(scheduled_url,)).fetchone()[0]=='off'
  # A completed dual prefetch still must not activate a source disabled meanwhile.
  dual=save('/dual');wait();dual_url=json.loads(row(dual['source_id'])['items'])[0]['url']
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode='pipeline',probe_workers=2);service.save_config(c,cfg)
   c.execute('insert or replace into route_modes values(?,?,?)',('CCTV1综合',dual_url,'off'))
  def prefetch(urls,*args):
   service.edit('source-state',dict(id=dual['source_id'],enabled=False))
   return {u:(copy.deepcopy(GOOD),time.monotonic(),False) for u in urls}
  with patch.object(service,'first_pass',side_effect=prefetch):service.run('probe',[dual_url])
  with service.db() as c:
   assert c.execute('select mode from route_modes where url=?',(dual_url,)).fetchone()[0]=='off'
   cfg=service.config(c);cfg.update(probe_mode='adaptive',probe_workers=1);service.save_config(c,cfg)
  # Each source mutation while a first pass is in flight invalidates its activation write.
  for action in ('edit','disable','delete','delete-and-recreate'):
   item=save('/probe-'+action);wait();source_id=item['source_id'];url=json.loads(row(source_id)['items'])[0]['url']
   old={'status':'未测','speed':50}
   with service.db() as c:
    c.execute('insert or replace into tests values(?,?)',(url,service.encode(old)))
    c.execute('insert or replace into route_modes values(?,?,?)',('CCTV1综合',url,'off'))
   entered=threading.Event();release=threading.Event()
   def probe(*args):entered.set();assert release.wait(6);return copy.deepcopy(GOOD)
   with patch.object(service,'probe',side_effect=probe),patch.object(service,'gateway_delay',return_value=0):
    api('job',dict(action='probe',url=url));assert entered.wait(2)
    if action=='edit':assert save('/changed',id=source_id)['refresh_queued']
    elif action=='disable':api('source-state',dict(id=source_id,enabled=False))
    else:
     api('delete',dict(id=source_id))
     if action=='delete-and-recreate':assert save('/replacement')['refresh_queued']
    release.set();wait()
   with service.db() as c:
    assert json.loads(c.execute('select result from tests where url=?',(url,)).fetchone()[0])==old,action
    assert c.execute('select mode from route_modes where url=?',(url,)).fetchone()[0]=='off',action
  # Saving after task reservation but before its worker starts is still protected.
  reserved=save('/reserved');wait();reserved_id=reserved['source_id'];reserved_url=json.loads(row(reserved_id)['items'])[0]['url']
  with service.db() as c:c.execute('insert or replace into route_modes values(?,?,?)',('CCTV1综合',reserved_url,'off'))
  with patch.object(service.threading,'Thread') as thread:
   service.start('probe',url=reserved_url)
   reserved_args=thread.call_args.kwargs['args']
  assert save('/reserved-new',id=reserved_id)['refresh_queued']
  with patch.object(service,'probe',side_effect=AssertionError('obsolete route probed')):service.run(*reserved_args);wait()
  with service.db() as c:assert c.execute('select mode from route_modes where url=?',(reserved_url,)).fetchone()[0]=='off'
  # A stale download cannot overwrite the latest source snapshot or resurrect deletion.
  held=save('/hold');assert Fixture.entered.wait(2);held_id=held['source_id']
  assert not next(s for s in api('state')['sources'] if s['id']==held_id)['pending_update']
  assert save('/latest',id=held_id)['refresh_queued']
  assert next(s for s in api('state')['sources'] if s['id']==held_id)['pending_update']
  Fixture.release.set();wait()
  assert not next(s for s in api('state')['sources'] if s['id']==held_id)['pending_update']
  assert '/latest' in row(held_id)['items'] and '/hold' not in row(held_id)['items']
  # Adding an unrelated source must not prevent a valid current result from activating.
  item=save('/valid');wait();url=json.loads(row(item['source_id'])['items'])[0]['url']
  entered=threading.Event();release=threading.Event()
  def valid(*args):entered.set();assert release.wait(6);return copy.deepcopy(GOOD)
  with patch.object(service,'probe',side_effect=valid),patch.object(service,'gateway_delay',return_value=0):
   api('route',dict(name='CCTV1综合',url=url,mode='off'))
   api('job',dict(action='probe',url=url));assert entered.wait(2)
   assert save('/logo',kind='logo')['refresh_queued']
   generation=row(item['source_id'])['version']
   assert save('/valid',id=item['source_id'],name='renamed',priority=20)['refresh_queued']
   assert row(item['source_id'])['version']==generation
   assert api('job',dict(action='refresh',source_id=item['source_id']))['refresh_queued']
   release.set();wait()
  with service.db() as c:assert not c.execute('select 1 from route_modes where url=?',(url,)).fetchone()
 finally:
  Fixture.release.set()
  for server in servers:server.shutdown();server.server_close()
print('通过：检测中编辑、队列持久化与合并、停用删除取消、失效更新丢弃、旧检测不覆盖手动状态、无关新增不干扰有效首测。')

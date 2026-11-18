"""独立更新和保存即更新：真实 HTTP、原子互斥、停用源、失败旧快照。"""
import json
import subprocess
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler
import app,service

class Source(BaseHTTPRequestHandler):
 hits=Counter();version=1;entered=threading.Event();release=threading.Event()
 def log_message(self,*_):pass
 def do_GET(self):
  type(self).hits[self.path]+=1
  if self.path=='/slow-fail':
   self.entered.set();self.release.wait(8)
  if self.path in ('/fail','/slow-fail'):
   self.send_response(503);self.end_headers();self.wfile.write(b'fixture unavailable');return
  if self.path in ('/guide','/guide2'):
   body=b'<tv><channel id="a"><display-name>CCTV1</display-name></channel><programme channel="a" start="20261118000000 +0800" stop="20261119000000 +0800"><title>fixture</title></programme></tv>'
  elif self.path=='/logo':body=json.dumps({'CCTV1':'https://logo.example.invalid/one.png'}).encode()
  else:body=f'#EXTM3U\n#EXTINF:-1,CCTV1\nhttps://stream.example.invalid/{self.path[1:]}/{self.version}.m3u8\n'.encode()
  self.send_response(200);self.end_headers();self.wfile.write(body)

with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)),patch.object(app,'DATA',Path(folder)),patch.object(service,'collect_eit',return_value=[]):
 service.init()
 with service.db() as c:
  cfg=service.config(c);cfg.update(minutes=0,auto_probe=True);service.save_config(c,cfg)
 upstream=ThreadingHTTPServer(('127.0.0.1',0),Source);server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
 for s in (upstream,server):threading.Thread(target=s.serve_forever,daemon=True).start()
 origin=f'http://127.0.0.1:{upstream.server_port}';base=f'http://127.0.0.1:{server.server_port}';op=build_opener(ProxyHandler({}))
 def api(path,data=None):
  req=Request(base+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
  with op.open(req,timeout=4) as r:return json.load(r)
 def wait():
  until=time.monotonic()+10
  while service.job['running'] and time.monotonic()<until:time.sleep(.01)
  assert not service.job['running'],'source update did not finish'
 def save(name,path,kind='playlist',**extra):
  result=api('source',{'name':name,'url':origin+path,'kind':kind,'priority':10,'interval_hours':0,**extra});assert result['refresh_started'];wait();return result['source_id']
 def row(sid):
  with service.db() as c:return dict(c.execute('select * from sources where id=?',(sid,)).fetchone())
 def reject(path,data):
  try:api(path,data);raise AssertionError('invalid operation accepted')
  except HTTPError as e:assert e.code==400
 try:
  with patch.object(service,'probe',side_effect=AssertionError('source update started detection')):
   ids={name:save(name,path,kind) for name,path,kind in [('A','/a','playlist'),('B','/b','playlist'),('节目','/guide','epg'),('台标','/logo','logo')]}
   assert all(Source.hits[p]==1 for p in ['/a','/b','/guide','/logo'])
   with service.db() as c:
    prior_url=json.loads(row(ids['A'])['items'])[0]['url']
    c.execute('insert into route_modes values(?,?,?)',('CCTV1综合',prior_url,'off'))
    c.execute('insert into tests values(?,?)',(prior_url,service.encode({'status':'保留','speed':800})))
    prior_modes=list(map(tuple,c.execute('select * from route_modes')));prior_tests=list(map(tuple,c.execute('select * from tests')))
   before=Source.hits.copy();untouched={k:row(v) for k,v in ids.items() if k!='A'}
   api('job',{'action':'refresh','source_id':ids['A']});wait()
   assert Source.hits['/a']==before['/a']+1 and all(Source.hits[p]==before[p] for p in ['/b','/guide','/logo'])
   assert all(row(ids[k])==v for k,v in untouched.items())
   # Source edit commits the new URL before its automatic worker reads it.
   Source.version=2;before=Source.hits.copy()
   save('A renamed','/new-a',id=ids['A'])
   assert Source.hits['/new-a']==1 and all(Source.hits[p]==before[p] for p in ['/a','/b','/guide','/logo'])
   assert '/new-a/2.m3u8' in row(ids['A'])['items']
   old=row(ids['A'])['items']
   api('source',{'id':ids['A'],'name':'A saved','kind':'playlist','url':origin+'/slow-fail','priority':10,'interval_hours':0})
   assert Source.entered.wait(2)
   assert row(ids['A'])['items']==old,'saving removed the old snapshot before fetching'
   assert api('job',{'action':'refresh','source_id':ids['A']})['refresh_queued']
   queued=api('source',{'name':'queued','kind':'logo','url':origin+'/logo','priority':10,'interval_hours':0})
   assert queued['refresh_queued']
   assert len(api('state')['sources'])==5
   Source.release.set();wait();assert row(ids['A'])['items']==old and '失败' in row(ids['A'])['last']
   # A disabled feed may be explicitly refreshed but must stay disabled.
   api('source-state',{'id':ids['B'],'enabled':False});before=Source.hits.copy()
   api('job',{'action':'refresh','source_id':ids['B']});wait();assert Source.hits['/b']==before['/b']+1 and row(ids['B'])['enabled']==0
   save('B disabled','/b','playlist',id=ids['B'],enabled=False);assert row(ids['B'])['enabled']==0
   # Failed same-type EPG/logo URL edits keep their active metadata snapshots.
   for name,kind in [('节目','epg'),('台标','logo')]:
    with service.db() as c:before_catalog=service.catalog(c);before_epg=service.epg(c,before_catalog)
    old_logo=row(ids[name])['logos'];save(name,'/fail',kind,id=ids[name])
    assert '失败' in row(ids[name])['last']
    if kind=='logo':assert row(ids[name])['logos']==old_logo
    else:
     with service.db() as c:assert service.epg(c,service.catalog(c))==before_epg
   for data in [dict(action='probe',source_id=ids['A']),dict(action='refresh',source_id=True),dict(action='refresh',source_id=999),dict(action='refresh',source_id=ids['A'],urls=[])]:reject('job',data)
   with service.db() as c:assert list(map(tuple,c.execute('select * from tests')))==prior_tests and list(map(tuple,c.execute('select * from route_modes')))==prior_modes
 finally:
  Source.release.set();wait();server.shutdown();upstream.shutdown()
# Actual UI helper must keep SVG content and spin only the source being updated.
subprocess.run(['node','-e',r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const source=fs.readFileSync('ui.js','utf8');
function button(id){return {dataset:{sourceRefresh:String(id)},innerHTML:'<svg>refresh</svg>',classes:new Set(),attrs:{},classList:{toggle(name,on){on?this.owner.classes.add(name):this.owner.classes.delete(name)}},setAttribute(k,v){this.attrs[k]=v}}}
const buttons=[button(1),button(2)];buttons.forEach(b=>b.classList.owner=b);
const newSource={},edit={},ctx=vm.createContext({busy:true,refreshingSources:new Set(),$:()=>newSource,document:{querySelectorAll:q=>q==='[data-feed]'?[edit]:buttons}});
vm.runInContext(source.slice(source.indexOf('function syncSourceActions('),source.indexOf('async function status()')),ctx);
ctx.syncSourceActions({action:'refresh',source_id:2,active_source_id:2});
assert(!buttons[0].disabled&&buttons[1].disabled);assert(!buttons[0].classes.has('is-updating')&&buttons[1].classes.has('is-updating'));assert.equal(buttons[1].innerHTML,'<svg>refresh</svg>');assert(!newSource.disabled&&!edit.disabled);
ctx.busy=false;ctx.syncSourceActions({action:'refresh',source_id:2});assert(buttons.every(b=>!b.disabled&&!b.classes.has('is-updating')));
ctx.busy=true;ctx.syncSourceActions({action:'scheduled',active_source_id:1});assert(buttons[0].classes.has('is-updating')&&!buttons[1].classes.has('is-updating'));
ctx.syncSourceActions({action:'scheduled',active_source_id:1,probing:true});assert(buttons.every(b=>!b.classes.has('is-updating')));
ctx.syncSourceActions({action:'probe',probing:true,queued_sources:[1]});assert(buttons[0].disabled&&!buttons[1].disabled);
assert(source.includes('return api(\'source\''));
console.log('通过：单源刷新图标、排队禁用、编辑可用与完成恢复。');
"""],check=True)
subprocess.run(['node','-e',r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const text=fs.readFileSync('ui.js','utf8'),editor={dataset:{}},fields={firstElementChild:{}},dialog={open:true,close(){this.open=false}},elements={editor,fields,dialog,channelEdit:{hidden:true},channelSettings:{disabled:true}};
let calls=0,release,message='';const wait=new Promise(resolve=>release=resolve);
const ctx=vm.createContext({$:id=>elements[id],safely:fn=>fn,editing:async()=>{calls++;await wait;return {refresh_queued:true}},status:async()=>{},reload:async()=>{},notice:text=>message=text});
vm.runInContext(text.slice(text.indexOf("$('editor').onsubmit="),text.indexOf('let settingsDirty=')),ctx);
(async()=>{const event={preventDefault(){},target:editor,currentTarget:editor};const first=editor.onsubmit(event);await editor.onsubmit(event);assert.equal(calls,1);release();await first;assert.equal(editor.dataset.saving,'false');assert(message.includes('当前任务结束后更新'));assert(!dialog.open);console.log('通过：任务中订阅表单可保存、保存期间防重复提交与排队提示。')})().catch(error=>{console.error(error);process.exitCode=1});
"""],check=True)
print('通过：三类订阅保存后自动单独更新、目标隔离、停用源不启用、重复请求互斥、旧快照兜底、错误目标拒绝、不触发测速。')

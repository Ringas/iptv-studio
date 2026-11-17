"""Multi-channel transitions through real HTTP APIs and actual UI selection functions."""
import copy,json,os,subprocess,tempfile,threading
from pathlib import Path
from unittest.mock import patch
from urllib.request import build_opener,ProxyHandler,Request
from http.server import ThreadingHTTPServer
import service
from app import Handler
from catalog import export
from verify_qualification_views import record

def main():
 snapshots=[]
 with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
  service.init()
  with service.db() as c:
   cfg=service.config(c);cfg.update(minutes=0,auto_probe=False,probe_mode='pipeline',probe_workers=1);service.save_config(c,cfg)
   items=[]
   for name,count in [('Alpha',2),('Beta',3),('Gamma',4)]:
    for i in range(count):
     url=f'http://fixture.invalid/{name}/{i}'
     items.append(dict(name=name,url=url,attrs={},options=[]))
     result=record(speed=150+i*100);result.update(quality='流畅',stalls=0,recent=[1]*(i+1),direct_history=[1]*(i+1))
     result['resolution']={'width':1920 if i==0 else 1280,'height':1080 if i==0 else 720}
     c.execute('insert into tests values(?,?)',(url,service.encode(result)))
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode(items),))
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  base=f'http://127.0.0.1:{server.server_port}';op=build_opener(ProxyHandler({}))
  def request(path,data=None):
   req=Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
   with op.open(req) as r:return r.read().decode()
  def check():
   state=json.loads(request('/api/state'));out=json.loads(request('/live.json'));expected=[]
   for ch in state['channels']:
    enabled=[r for r in ch['lines'] if r['enabled']]
    assert ch['lines'][:len(enabled)]==enabled
    if enabled:expected.append((ch['name'],[r['url'] for r in (enabled[:1] if state['settings']['mode']=='best' else enabled)]))
   assert expected==[(c['name'],[r['url'] for r in c['lines']]) for c in out]
   assert [l for l in request('/live.m3u').splitlines() if l and not l.startswith('#')]==[u for _,urls in expected for u in urls]
   assert request('/live.txt')==export('txt',out,base+'/guide.xml')
   snapshots.append(dict(state=state,exported=out));return state
  try:
   state=check()
   for publication in ('merge','best'):
    for criteria,speed,height in [('both',100,720),('both',200,720),('resolution',100,1080),('speed',400,720),('both',1000,2160),('both',100,720)]:
     request('/api/settings',dict(hours=0,mode=publication,strict=True,auto_probe=False,probe_mode='pipeline',criteria=criteria,min_speed=speed,min_height=height));state=check()
   # Individual manual changes must reorder immediately and survive settings changes.
   for ch in state['channels']:
    first=ch['lines'][0]['url'];request('/api/route',dict(name=ch['name'],url=first,mode='off'));changed=check()
    current=next(c for c in changed['channels'] if c['name']==ch['name'])
    assert current['lines'][0]['url']!=first
    request('/api/route',dict(name=ch['name'],url=first,mode='on'));check()
   for ch in state['channels']:
    for r in ch['lines']:request('/api/route',dict(name=ch['name'],url=r['url'],mode='off'))
   assert not check()['lines']
   request('/api/settings',dict(hours=0,mode='merge',strict=True,auto_probe=False,probe_mode='pipeline',criteria='both',min_speed=50,min_height=480));assert not check()['lines']
   # A completed effective first pass resets only the tested manual route and reorders it.
   url=items[-1]['url'];good=record(speed=1200);good.update(quality='流畅',stalls=0)
   with patch.object(service,'probe',return_value=copy.deepcopy(good)),patch.object(service,'gateway_delay',return_value=0):
    service.job.update(running=True,stop_queue=False,stop_line=False);service.run('probe',[url])
   state=check();ch=next(c for c in state['channels'] if c['name']=='GAMMA');assert ch['lines'][0]['url']==url and ch['lines'][0]['enabled']
   # Deferred 429 keeps previous evidence/control rather than promoting a failed observation.
   with patch.object(service,'probe',return_value={'quality':'未能验证','httpStatus':429,'deferred':True,'note':['rate limited']}),patch.object(service,'gateway_delay',return_value=0):
    service.job.update(running=True,stop_queue=False,stop_line=False);service.run('probe',[url])
   check()
   path=Path(folder)/'snapshots.json';path.write_text(json.dumps(snapshots))
   subprocess.run([os.getenv('NODE_BIN','node'),'verify_route_consistency.cjs',str(path)],check=True)
  finally:server.shutdown();server.server_close();thread.join()
 print('PASS: 3 channels, 2/3/4 routes, threshold changes, manual controls, completed probe, 429 and all export formats.')
if __name__=='__main__':main()

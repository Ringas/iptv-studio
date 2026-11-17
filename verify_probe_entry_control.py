"""All four task entry points share first-pass control semantics via real HTTP."""
import copy,json,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from http.server import ThreadingHTTPServer
import service
from app import Handler
from verify_dual_schedule import GOOD

def main():
 with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
  service.init();urls=[f'http://entry{i}.invalid/live' for i in range(3)]
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode='pipeline',probe_workers=1,strict=True,auto_probe=True);service.save_config(c,cfg)
   c.execute("insert into sources(name,kind,enabled,priority,next_update,items) values('fixture','playlist',1,1,1,?)",(service.encode([dict(name=f'Fixture{i}',url=u,attrs={},options=[]) for i,u in enumerate(urls)]),))
   names={r['url']:r['members'][0] for ch in service.catalog(c) for r in ch['lines']}
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  base=f'http://127.0.0.1:{server.server_port}'
  def request(path,payload=None):
   with urlopen(Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'}),timeout=5) as r:return json.load(r)
  def probe(u,*args):return {**copy.deepcopy(GOOD),'speed':50 if u==urls[1] else 800}
  cases=[({'action':'scheduled'},urls),({'action':'probe'},urls),({'action':'probe','urls':urls[:2]},urls[:2]),({'action':'probe','url':urls[0]},urls[:1])]
  try:
   for measured in (False,True):
    for payload,expected in cases:
     with service.db() as c:
      c.execute('delete from tests');c.execute('delete from route_modes');c.execute('update sources set next_update=1')
      for i,u in enumerate(urls):
       c.execute('insert into route_modes values(?,?,?)',(names[u],u,'on' if i==1 else 'off'))
       if measured:c.execute('insert into tests values(?,?)',(u,service.encode(GOOD)))
     with patch.object(service,'probe',side_effect=probe) as spy,patch.object(service,'gateway_delay',return_value=0),patch.object(service,'refresh',return_value=(set(urls),[])):
      request('/api/job',payload)
      deadline=time.monotonic()+5
      while service.job['running'] and time.monotonic()<deadline:time.sleep(.01)
      assert not service.job['running'] and '任务中断' not in service.job['message'],service.job
      assert {call.args[0] for call in spy.call_args_list}==set(expected)
     rows={r['url']:r for ch in request('/api/state')['channels'] for r in ch['lines']}
     for i,u in enumerate(urls):
      assert rows[u]['mode']==('auto' if u in expected else 'on' if i==1 else 'off')
      assert rows[u]['enabled']==(i!=1 if u in expected else i==1)
     exported={r['url'] for ch in request('/live.json') for r in ch['lines']}
     assert exported=={u for u,r in rows.items() if r['published']}
  finally:server.shutdown();server.server_close();thread.join()
 assert '>恢复自动</button>' not in (Path(__file__).parent/'ui.js').read_text()
 print('PASS: scheduled/all/selected/single HTTP entry points, tested+untested manual choices reset only for measured targets, unselected states preserved, export consistency, no restore-auto button.')
if __name__=='__main__':main()

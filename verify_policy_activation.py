"""新门槛仅用于下一次测定；保存不改既有发布，未验证尝试也不冒充新结论。"""
import copy,json,tempfile,threading
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request,build_opener,ProxyHandler
import service
from app import Handler
from verify_qualification_views import record
from catalog import selected


def main():
 with tempfile.TemporaryDirectory() as temp,patch.object(service,'DATA',Path(temp)),patch.object(service,'gateway_delay',return_value=0):
  service.init();urls={key:f'http://{key}.invalid/live' for key in ['hd','slow','legacy','on','off']}
  tests={'hd':record(speed=180),'slow':record(speed=80,assessment='速率未达标'),'legacy':record(speed=300),'on':record(speed=80,assessment='速率未达标'),'off':record(speed=200)}
  tests['hd']['resolution']={'width':1920,'height':1080};tests['legacy'].pop('evidence')
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode='pipeline',probe_workers=1);service.save_config(c,cfg)
   items=[dict(name='Fixture',url=url,attrs={},options=[]) for url in urls.values()]
   c.execute("insert into sources(name,kind,url,priority,enabled,items,next_update) values('示例订阅','playlist','https://example.invalid/sub',1,1,?,1)",(service.encode(items),))
   for key,value in tests.items():c.execute('insert into tests values(?,?)',(urls[key],service.encode(value)))
   name=service.catalog(c)[0]['name']
   for mode in ('on','off'):c.execute('insert into route_modes values(?,?,?)',(name,urls[mode],mode))
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  op=build_opener(ProxyHandler({}));base=f'http://127.0.0.1:{server.server_port}'
  def request(path,payload=None):
   with op.open(Request(base+path,data=None if payload is None else json.dumps(payload).encode(),headers={'Content-Type':'application/json'}),timeout=5) as response:return response.read()
  def save(speed=100,height=720,criteria='both'):
   request('/api/settings',dict(hours=8,auto_probe=True,strict=True,mode='merge',probe_mode='pipeline',criteria=criteria,min_speed=speed,min_height=height))
  def state():
   result=json.loads(request('/api/state'));rows={r['url']:r for ch in result['channels'] for r in ch['lines']}
   exported=json.loads(request('/live.json'));expected=[r['url'] for ch in result['channels'] for r in ch['lines'] if r['published']]
   assert [r['url'] for ch in exported for r in ch['lines']]==expected
   assert [line for line in request('/live.m3u').decode().splitlines() if line and not line.startswith('#')]==expected
   txt=request('/live.txt').decode();assert {u for u in urls.values() if u in txt}==set(expected)
   return result,rows
  def snapshot(rows):return {u:(r['assessment'],r['mode'],r['enabled'],r['published']) for u,r in rows.items()}
  def run(urls_to_test,response,action='probe'):
   service.job.update(running=True,stop_line=False,stop_queue=False)
   with patch.object(service,'probe',side_effect=response if callable(response) else lambda *_:copy.deepcopy(response)):
    service.run(action,urls_to_test)
   assert not service.job['message'].startswith('任务中断'),service.job
  try:
   before,rows=state();original=snapshot(rows);export_before=request('/live.m3u')
   for speed,height,criteria in [(1000,2160,'both'),(50,480,'both'),(999,2160,'resolution')]:
    save(speed,height,criteria);_,rows=state();assert snapshot(rows)==original
    assert request('/live.m3u')==export_before
   with service.db() as c:
    saved=json.loads(c.execute('select result from tests where url=?',(urls['legacy'],)).fetchone()[0]);assert saved.pop('result_assessment')=='保留';assert saved==tests['legacy']
   save(50,480);run([urls['slow']],record(speed=80))
   _,rows=state();assert rows[urls['slow']]['enabled']
   assert rows[urls['hd']]['assessment']=='保留'
   save(1000,2160);run([urls['hd']],record(speed=180))
   result,rows=state();assert not rows[urls['hd']]['enabled']
   assert result['channels'][0]['lines'][0]['url']==urls['legacy']
   assert rows[urls['legacy']]['assessment']=='保留'
   save(50,480)
   with patch.object(service,'refresh',return_value=({urls['legacy']},[])):
    run(None,record(speed=300),action='scheduled')
   _,rows=state();assert rows[urls['legacy']]['assessment']=='保留'
   assert rows[urls['on']]['mode']=='on' and rows[urls['off']]['mode']=='off'
   save(2000,2160);stable=snapshot(rows)
   for pending in [{'httpStatus':429,'quality':'未能验证','deferred':True,'note':['limited']},{'quality':'未能验证','retryable':False,'note':['request timeout']}]:
    run([urls['legacy']],pending);_,rows=state();assert snapshot(rows)==stable
   seen=[]
   def changing_settings(url,*_):
    seen.append(service.job['probe_policy'].copy())
    if len(seen)==1:save(10,1)
    return record(speed=300)
   run([urls['legacy'],urls['slow']],changing_settings)
   assert seen==[dict(criteria='both',min_speed=2000,min_height=2160)]*2
   _,rows=state()
   for key in ('legacy','slow'):
    assert rows[urls[key]]['assessment']=='速率未达标'
    assert not rows[urls[key]]['enabled']
   stable=snapshot(rows);service.init();_,rows=state();assert snapshot(rows)==stable
   print('通过：保存不重算、新测使用新规则、保留既有结论、计划测定、429/超时保留、任务中途改规则与重启导出一致')
  finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()

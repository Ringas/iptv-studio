"""Offline concurrent scheduling, isolation, cancellation and persistence checks."""
import copy,json,os,tempfile,threading,time
from pathlib import Path
from unittest.mock import patch
import service
from dual_probe import context,active_states
GOOD={'quality':'流畅','method':'continuous','opening':'直连可播','advanced':True,'speed':800,'seconds':24,'resolution':{'height':720},'note':[], 'evidence':{'decode':{'state':'passed','frames':6,'pts_advanced':True},'live':{'state':'passed'}}}

def exercise(items,callback,stop=None,seed=False):
 with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)),patch.dict(os.environ,{'IPTV_PROBE_WORKERS':'2'}):
  service.init();service.gateway_windows.clear();service.gateway_limits.clear()
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode='pipeline',criteria='both',min_speed=100,min_height=720);service.save_config(c,cfg)
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name=str(i),url=u,attrs={},options=[]) for i,u in enumerate(items)]),))
   if seed:
    for u in items:c.execute('insert into tests values(?,?)',(u,service.encode(GOOD)))
  calls=[];running=set();peak=0;guard=threading.Lock();real_first=service.first_pass;cooldowns={}
  def cooling(u,seconds):cooldowns[service.urlsplit(u).hostname]=seconds if seconds and seconds>30 else 0
  def delay(u,reserve=False):return cooldowns.get(service.urlsplit(u).hostname,0)
  def first(*args):
   result=real_first(*args)
   # Avoid eight seconds wall sleep in unit test; verify scheduling uses the recorded completion.
   return {u:(r,finished-9,stopped) for u,(r,finished,stopped) in result.items()}
  def probe(u,m,o):
   nonlocal peak
   with guard:
    assert not (m=='continuous' and running),(m,running)
    assert all(service.urlsplit(a).hostname!=service.urlsplit(u).hostname for a in running)
    running.add(u);peak=max(peak,len(running));calls.append((u,m,'start',time.monotonic()))
   try:
    if stop and m=='pipeline':stop(u)
    time.sleep(.04)
    json.dumps(service.job) # API progress must never contain a process/lock object.
    return copy.deepcopy(callback(u,m))
   finally:
    with guard:running.remove(u);calls.append((u,m,'end',time.monotonic()))
  service.job.update(running=True,run_id='test',stop_queue=False,stop_line=False,active_lines={})
  with patch.object(service,'probe',side_effect=probe),patch.object(service,'first_pass',side_effect=first),patch.object(service,'gateway_delay',side_effect=delay),patch.object(service,'cool_gateway',side_effect=cooling):service.run('probe',items)
  assert '任务中断' not in service.job['message'],service.job
  assert not active_states and not service.job['active_lines']
  with service.db() as c:results={r['url']:json.loads(r['result']) for r in c.execute('select * from tests')}
  return calls,results,peak

def main():
 a,b,c='http://one.test/a','http://two.test/b','http://one.test/c'
 calls,results,peak=exercise([a,b,c],lambda *_:GOOD)
 assert peak==2 and len([x for x in calls if x[2]=='start'])==3
 for r in results.values():assert r['qualification_history']==[1] and r['evidence']['qualification']['state']=='passed'
 variants=[{**GOOD,'speed':100},{**GOOD,'resolution':{'height':480}}, {'quality':'未能验证','retryable':True,'note':['timeout']}, {'quality':'未能验证','retryable':True,'retry_deferred':'gateway_isolation','note':['网关被占用']}]
 for initial in variants:
  calls,results,_=exercise([a,b],lambda u,m:initial if u==a and m=='pipeline' else GOOD)
  assert [x[1] for x in calls if x[0]==a and x[2]=='start']==['pipeline','continuous']
  start=next(x[3] for x in calls if x[1]=='continuous' and x[2]=='start')
  assert start>=max(x[3] for x in calls if x[1]=='pipeline' and x[2]=='end')
  assert results[a]['qualification_history']==[1] and results[a]['entry_retries']==1,results[a]
 # 429 > 30 seconds does not launch a request merely to satisfy the isolation queue.
 limited={'httpStatus':429,'retryAfter':300,'note':['限流']}
 calls,results,_=exercise([a,b],lambda u,m:limited if u==a else GOOD,seed=True)
 assert results[a].get('last_attempt',{}).get('httpStatus')==429
 assert len([x for x in calls if x[0]==a and x[2]=='start'])==1
 # Skipping one active route preserves its history, while the other completes normally.
 def skip(u):
  if u==a:service.stop_probe('test',a)
 calls,results,_=exercise([a,b],lambda *_:GOOD,stop=skip,seed=True)
 assert results[a]==GOOD and results[b]['qualification_history']==[1]
 def stopall(u):service.stop_probe('test')
 calls,results,_=exercise([a,b,c],lambda *_:GOOD,stop=stopall,seed=True)
 assert all(r==GOOD for r in results.values())
 print('PASS: two slots, same-host serial, full isolation for low speed/height/timeout/contention, one history, serializable progress, skip/stop preservation.')
if __name__=='__main__':main()

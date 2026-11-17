"""Scheduled first verdict resets manual mode; same-job retries preserve activation."""
import copy,json,tempfile
from pathlib import Path
from unittest.mock import patch
import service
from catalog import selected
from verify_dual_schedule import GOOD
from verify_probe_schedule import Clock

def case(manual,results,expected_mode,enabled,assessment,workers=1,action='scheduled',next_result=None,strict=True):
 with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
  service.init();url='https://control.invalid/live';clock=Clock()
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode='pipeline',probe_workers=workers,strict=strict);service.save_config(c,cfg)
   c.execute("insert into sources(name,kind,enabled,priority,next_update,items) values('fixture','playlist',1,1,1,?)",(service.encode([dict(name='Fixture',url=url,attrs={},options=[])]),))
   name=service.catalog(c)[0]['name']
   c.execute('insert into route_modes values(?,?,?)',(name,url,manual))
  original_first=service.first_pass
  def first(*a):return {u:(r,clock.read(),stop) for u,(r,at,stop) in original_first(*a).items()}
  def run(items):
   with service.db() as c:c.execute('update sources set next_update=1')
   service.job.update(running=True,stop_queue=False,stop_line=False)
   with patch.object(service,'refresh',return_value=({url},[])),patch.object(service,'probe',side_effect=copy.deepcopy(items)) as probe,patch.object(service,'gateway_delay',return_value=0),patch.object(service,'cool_gateway'),patch.object(service,'first_pass',side_effect=first),patch.object(service.time,'monotonic',clock.read),patch.object(service.time,'sleep',clock.sleep):
    service.run(action,None)
   assert probe.call_count==len(items),(probe.call_count,service.job)
   assert '任务中断' not in service.job['message'],service.job
   with service.db() as c:
    ch=service.catalog(c)[0];r=ch['lines'][0]
    assert bool(list(selected([ch],service.config(c))))==r['enabled']
    return r
  r=run(results)
  assert (r['mode'],r['enabled'],r['assessment'])==(expected_mode,enabled,assessment),r
  if len(results)>1:
   assert r['test'].get('recheck_control') and any('异常复测仅更新' in n for n in r['test']['note']),r
  if next_result:
   r=run([next_result])
   assert not r['test'].get('recheck_control') and r['enabled']==(next_result['speed']>100),r

def main():
 good=copy.deepcopy(GOOD);low={**copy.deepcopy(GOOD),'speed':50}
 unstable={**copy.deepcopy(GOOD),'quality':'可播但不稳','stalls':1}
 case('off',[good],'auto',True,'保留')
 case('off',[good],'off',False,'保留',strict=False)
 case('on',[low],'auto',False,'速率未达标')
 case('off',[good],'auto',True,'保留',action='probe')
 case('off',[{'quality':'未能验证','note':['request timeout']},good],'off',False,'保留')
 case('on',[{'httpStatus':429,'note':['limited']},good],'on',True,'保留')
 case('off',[unstable,low],'auto',True,'速率未达标',next_result=low)
 case('on',[low,good],'auto',False,'保留',workers=2,next_result=good)
 print('PASS: scheduled reset, explicit probe resets manual, timeout/429 recovery, opposing retry results preserve activation, dual isolation, next-job reset and exports.')
if __name__=='__main__':main()

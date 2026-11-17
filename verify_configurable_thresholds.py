"""Cross-layer configurable qualification; fictional source, isolated storage."""
import copy,tempfile,json
from pathlib import Path
from unittest.mock import patch
import service
from catalog import assess,selected
from verify_dual_schedule import GOOD

def main():
 with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
  service.init()
  with service.db() as c:
   cfg=service.config(c)
   assert (cfg['criteria'],cfg['min_speed'],cfg['min_height'])==('both',100,720)
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name='Fixture',url='http://fixture.invalid/a',attrs={},options=[])]),))
  payload=dict(hours=6,mode='merge',strict=True,auto_probe=False,criteria='both')
  for mode in ('pipeline','pipeline_dual','continuous'):
   service.edit('settings',{**payload,'probe_mode':mode,'min_speed':50,'min_height':480})
   with service.db() as c:cfg=service.config(c)
   result={**copy.deepcopy(GOOD),'speed':75,'resolution':{'height':480},'pipeline':True}
   assert assess(result,cfg)=='保留'
   assert assess({**result,'speed':50},cfg)=='速率未达标'
   assert assess({**result,'resolution':{'height':479}},cfg)=='分辨率未达标'
   assert assess({**result,'advanced':False,'coarse_rejected':True},cfg)=='未能验证'
   with service.db() as c:
    c.execute('insert or replace into tests values(?,?)',('http://fixture.invalid/a',service.encode(result)))
    assert list(selected(service.catalog(c),cfg))
   service.edit('settings',{**payload,'probe_mode':mode,'min_speed':100,'min_height':720})
   with service.db() as c:
    assert list(selected(service.catalog(c),service.config(c)))
  service.edit('settings',{**payload,'probe_mode':'pipeline','min_speed':50,'min_height':480})
  def probe(*args):
   assert service.job['probe_policy']==dict(criteria='both',min_speed=50,min_height=480)
   return copy.deepcopy(result)
  with patch.object(service,'probe',side_effect=probe),patch.object(service,'gateway_delay',return_value=0):
   service.job.update(running=True,stop_line=False,stop_queue=False)
   service.run('probe',['http://fixture.invalid/a'])
  assert '1 条通过' in service.job['message'],service.job
  for manual in ('on','off'):
   with service.db() as c:name=service.catalog(c)[0]['name']
   service.edit('route',dict(name=name,url='http://fixture.invalid/a',mode=manual))
   for speed in (1,10000):
    service.edit('route',dict(name=name,url='http://fixture.invalid/a',mode=manual))
    service.edit('settings',{**payload,'probe_mode':'pipeline','min_speed':speed,'min_height':480})
    with service.db() as c:assert service.catalog(c)[0]['lines'][0]['mode']==manual
    with patch.object(service,'probe',return_value=copy.deepcopy(result)),patch.object(service,'gateway_delay',return_value=0):
     service.job.update(running=True,stop_line=False,stop_queue=False)
     service.run('probe',['http://fixture.invalid/a'])
    with service.db() as c:
     ch=service.catalog(c)[0];line=ch['lines'][0]
     assert line['mode']=='auto' and line['enabled']==(speed<result['speed'])
     assert bool(list(selected([ch],service.config(c))))==(speed<result['speed'])
   service.edit('route',dict(name=name,url='http://fixture.invalid/a',mode='auto'))
   with service.db() as c:assert not service.catalog(c)[0]['lines'][0]['enabled']
  for criteria,height in [('both','480'),('resolution','480'),('speed','0')]:
   service.job.update(pipeline=True,stop_line=False,stop_queue=False,probe_policy=dict(criteria=criteria,min_speed=50,min_height=480))
   with patch.object(service.subprocess,'Popen') as spawn:
    proc=spawn.return_value.__enter__.return_value
    proc.stdout=[json.dumps(result)];proc.wait.return_value=0;proc.poll.return_value=0
    service.probe('http://fixture.invalid/a','pipeline',[])
    env=spawn.call_args.kwargs['env']
    assert env['IPTV_PROBE_MIN_HEIGHT']==height
    assert json.loads(env['IPTV_PROBE_POLICY'])==service.job['probe_policy']
 print('PASS: defaults, saved thresholds across modes, boundaries, publication, incomplete evidence and task policy.')
if __name__=='__main__':main()

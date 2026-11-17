"""Full/scheduled scope and recovery: real catalog + scheduler + isolated DB."""
import copy,json,tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch
import service
from catalog import selected
from verify_dual_schedule import GOOD

def exercise(mode,workers,action='probe',requested=None):
 with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
  service.init()
  labels=['unmeasured','slow','low','manualoff','manualon','hiddenline','hiddenchannel','hiddengroup']
  urls={k:f'https://{k}.invalid/live' for k in labels+['inactive','later']}
  # One channel has two failing routes, not merely one failing channel per URL.
  def item(k,name=None):return dict(name=name or ('slow' if k=='low' else k),url=urls[k],attrs={},options=[])
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode=mode,probe_workers=workers,criteria='both',min_speed=100,min_height=720,strict=True,auto_probe=True)
   service.save_config(c,cfg)
   for source,enabled,due,items in [('due',1,1,[*(item(k) for k in labels),item('unmeasured','duplicate')]),('inactive',0,1,[item('inactive')]),('later',1,9999999999,[item('later')])]:
    c.execute('insert into sources(name,kind,priority,enabled,next_update,items) values(?,\'playlist\',1,?,?,?)',(source,enabled,due,service.encode(items)))
   for k,test in [('slow',{**GOOD,'speed':1}),('low',{**GOOD,'resolution':{'height':180}})]:
    c.execute('insert into tests values(?,?)',(urls[k],service.encode(test)))
   names={r['url']:ch['name'] for ch in service.catalog(c) for r in ch['lines']}
   c.execute('insert into route_modes values(?,?,?)',(names[urls['manualon']],urls['manualon'],'on'))
  service.edit('channels',dict(names=[names[urls['manualoff']]],enabled=False))
  with service.db() as c:
   c.execute('insert into hidden_items values(?,?,?)',('route',names[urls['hiddenline']],urls['hiddenline']))
   c.execute('insert into hidden_items values(?,?,?)',('channel',names[urls['hiddenchannel']],''))
   c.execute('insert into rules values(?,?)',(names[urls['hiddengroup']],service.encode({'group':'Hidden fixture'})))
   c.execute('insert into hidden_items values(?,?,?)',('group','Hidden fixture',''))
   before_modes=[tuple(r) for r in c.execute('select * from route_modes order by name,url')]
   before_rules=[tuple(r) for r in c.execute('select * from rules order by name')]
   channels=service.catalog(c)
   failed=next(ch for ch in channels if any(r['url']==urls['slow'] for r in ch['lines']))
   assert len(failed['lines'])==2 and not failed['enabled']
   assert all(not ch['enabled'] for ch in channels if any(r['url'] in [urls[k] for k in ('unmeasured','slow','low','manualoff')] for r in ch['lines']))
  calls=[];refreshed=[]
  def probe(u,m,options):
   calls.append(u)
   return {**copy.deepcopy(GOOD),'seconds':8 if m=='legacy' else 24,'method':'legacy' if m=='legacy' else 'continuous'}
  def refresh(source,attempts):
   refreshed.append(source['name'])
   return {it['url'] for it in json.loads(source['items'])},[]
  targets=None if requested is None else [urls[k] for k in requested]
  service.job.update(running=True,stop_queue=False,stop_line=False)
  with patch.object(service,'probe',side_effect=probe),patch.object(service,'gateway_delay',return_value=0),patch.object(service,'refresh',side_effect=refresh):
   service.run(action,targets)
  assert '任务中断' not in service.job['message'],service.job
  expected={urls[k] for k in ('unmeasured','slow','low','manualoff','manualon')}
  if action=='probe':expected.add(urls['later'])
  else:assert refreshed==['due'],refreshed
  if requested is not None:expected=set(targets)
  assert Counter(calls)==Counter({u:1 for u in expected}),(mode,workers,action,calls,expected)
  with service.db() as c:
   assert [r for r in before_modes if r[1] not in expected]==[tuple(r) for r in c.execute('select * from route_modes order by name,url')]
   assert before_rules==[tuple(r) for r in c.execute('select * from rules order by name')]
   channels=service.catalog(c)
   exported={r['url'] for ch in selected(channels,service.config(c)) for r in ch['lines']}
   for ch in channels:
    for r in ch['lines']:
     if r['url'] not in expected:continue
     assert r['assessment']=='保留',r
     assert r['enabled'] is True,r
     assert (r['url'] in exported)==r['enabled']
   tested={r[0] for r in c.execute('select url from tests')}
   assert not tested.intersection(urls[k] for k in ('hiddenline','hiddenchannel','hiddengroup','inactive'))
  assert service.job['total']==len(expected) and service.job['done']==len(expected)

def main():
 for mode,workers in [('adaptive',1),('continuous',1),('legacy',1),('pipeline',1),('pipeline',2)]:
  exercise(mode,workers)
  exercise(mode,workers,'scheduled')
 exercise('pipeline',1,requested=['slow'])
 exercise('pipeline',2,requested=[])
 print('PASS: 12 full/scheduled/explicit scenarios; unmeasured+failed recovery, all entry points reset tested manual states, hidden/inactive excluded, scheduled scope, URL deduplication and exports.')
if __name__=='__main__':main()

"""Historical/current/control projections agree across API and public exports."""
import copy,json,tempfile,threading
from pathlib import Path
from urllib.request import urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import service
from app import Handler
from catalog import assess,policy_changed
from verify_dual_schedule import GOOD

def record(speed=150,threshold=100,assessment='保留'):
 return {**copy.deepcopy(GOOD),'pipeline':True,'speed':speed,'resolution':{'width':1280,'height':720},'at':'2026-11-01T00:00:00Z',
  'evidence':{**copy.deepcopy(GOOD['evidence']),'qualification':dict(state='passed' if assessment=='保留' else 'failed',assessment=assessment,criteria='both',min_speed=threshold,min_height=720)}}

def main():
 with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
  service.init()
  labels=['auto','on','off','oldfailed','legacy','coarse','grace']
  urls={k:f'http://{k}.invalid/live' for k in labels}
  records={k:record() for k in labels}
  records['oldfailed']=record(threshold=200,assessment='速率未达标')
  records['legacy'].pop('evidence')
  records['coarse'].update(advanced=False,coarse_rejected=True)
  records['grace'].update(quality='暂时失败',stable_qualified=True,grace=True)
  with service.db() as c:
   c.execute("insert into sources(name,kind,priority,enabled,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name=k,url=u,attrs={},options=[]) for k,u in urls.items()]),))
   for k,r in records.items():c.execute('insert into tests values(?,?)',(urls[k],service.encode(r)))
   names={r['url']:ch['name'] for ch in service.catalog(c) for r in ch['lines']}
   for mode in ('on','off'):c.execute('insert into route_modes values(?,?,?)',(names[urls[mode]],urls[mode],mode))
   original=[tuple(r) for r in c.execute('select * from tests order by url')]
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  base=f'http://127.0.0.1:{server.server_port}'
  def get(path):
   with urlopen(base+path) as r:return r.read().decode()
  def views():return {r['url']:r for ch in json.loads(get('/api/state'))['channels'] for r in ch['lines']}
  def check_exports(rows):
   exported={r['url']:r for ch in json.loads(get('/live.json')) for r in ch['lines']}
   expected={u for u,r in rows.items() if r['published']}
   assert set(exported)==expected
   for u,r in exported.items():
    for key in ('detection','current_qualification','assessment','mode','enabled','published'):assert r[key]==rows[u][key]
   for path in ('/live.m3u','/live.txt'):
    text=get(path)
    assert {u for u in urls.values() if u in text}==expected
  try:
   for threshold in (100,200,50):
    service.edit('settings',dict(hours=6,mode='merge',probe_mode='pipeline',auto_probe=False,strict=True,criteria='both',min_speed=threshold,min_height=720))
    rows=views();check_exports(rows)
    for label in ('auto','on','off'):
     r=rows[urls[label]]
     assert r['detection']['assessment']=='保留' and r['detection']['policy']['min_speed']==100
     assert r['current_qualification']['policy']['min_speed']==threshold
     assert r['current_qualification']['state']==('passed' if threshold<150 else 'failed')
     assert r['enabled']==(label=='on' or label=='auto' and threshold<150)
    assert rows[urls['oldfailed']]['detection']['assessment']=='速率未达标'
    assert rows[urls['oldfailed']]['current_qualification']['state']==('passed' if threshold<150 else 'failed')
    assert rows[urls['legacy']]['detection']['assessment']=='历史结论未知'
    assert rows[urls['coarse']]['current_qualification']['state']!='passed'
    assert rows[urls['grace']]['assessment']==('待复测' if threshold==100 else '未能验证')
    with service.db() as c:assert original==[tuple(r) for r in c.execute('select * from tests order by url')]
   # Latest inconclusive attempt is separate; never rewrites prior pass or manual on.
   service.job.update(running=True,stop_queue=False,stop_line=False)
   # Mark deferred to avoid retrying in this isolated policy test.
   with patch.object(service,'probe',return_value={'quality':'未能验证','httpStatus':429,'deferred':True,'note':['rate limited']}),patch.object(service,'gateway_delay',return_value=0):
    service.run('probe',[urls['on']])
   rows=views();r=rows[urls['on']];check_exports(rows)
   assert r['mode']=='on' and r['published'] and r['detection']['assessment']=='保留'
   assert r['current_qualification']['retained_after_attempt']
   assert r['test']['last_attempt']['evidence']['qualification']['min_speed']==50
   assert r['test']['last_attempt']['at']
   # Hidden inventory uses the same full evidence projection, but never exports it.
   with service.db() as c:
    c.execute('insert into hidden_items values(?,?,?)',('route',names[urls['on']],urls['on']))
    hidden=[dict(kind='route',name=names[urls['on']],url=urls['on'])];service.catalog(c,hidden_items=hidden)
    item=next(r for r in hidden if r['url']==urls['on'])
    assert item['detection']==r['detection'] and item['current_qualification']==r['current_qualification']
    assert item['enabled'] and not item['published']
   check_exports(views())
   # A new failed test must not inherit qualification history from a different policy.
   with service.db() as c:
    previous=record();previous.update(stable_qualified=True,qualification_history=[1])
    c.execute('insert or replace into tests values(?,?)',(urls['auto'],service.encode(previous)))
   service.edit('settings',dict(hours=6,mode='merge',probe_mode='pipeline',auto_probe=False,strict=True,criteria='both',min_speed=200,min_height=720))
   service.job.update(running=True,stop_queue=False,stop_line=False)
   with patch.object(service,'probe',return_value={'quality':'暂时失败','method':'continuous','retryable':False,'note':['decode failed']}),patch.object(service,'gateway_delay',return_value=0):
    service.run('probe',[urls['auto']])
   row=views()[urls['auto']]
   assert not row['enabled'] and not row['test']['stable_qualified']
   assert row['test']['qualification_history']==[0]
  finally:server.shutdown();server.server_close();thread.join()
 assert policy_changed(record(),dict(criteria='speed',min_speed=100,min_height=1080)) is True
 assert policy_changed(record(),dict(criteria='both',min_speed=100,min_height=720)) is False
 speed_only=record();speed_only['evidence']['qualification']['criteria']='speed'
 assert policy_changed(speed_only,dict(criteria='speed',min_speed=100,min_height=1080)) is False
 print('PASS: immutable history, raised/lowered policies, legacy unknowns, grace policy changes, manual overrides, deferred attempt, hidden inventory and API/JSON/M3U/TXT consistency.')
if __name__=='__main__':main()

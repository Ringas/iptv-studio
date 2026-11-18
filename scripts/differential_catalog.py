"""Run inside each image: exact deterministic catalogue and export snapshots."""
import copy,json,tempfile
from pathlib import Path
import service
from catalog import export,classify_result,assess
snapshots=[]
with tempfile.TemporaryDirectory() as temp:
 service.DATA=Path(temp);service.init()
 with service.db() as c:
  c.execute('delete from sources');items=[]
  for group in ('Alpha','Beta'):
   for index,(height,speed) in enumerate(((1080,300),(720,800),(480,75))):
    url=f'http://fixture.invalid/{group}/{index}';items.append(dict(name=group,url=url,attrs={},options=[]))
    r=dict(method='continuous',pipeline=True,quality='流畅',advanced=True,speed=speed,resolution={'width':height*16//9,'height':height},seconds=24,play_seconds=24,stalls=0,opening='直连可播',direct_history=[1],note=[],at='2026-11-01T00:00:00Z')
    c.execute('insert into tests values(?,?)',(url,service.encode(r)))
  c.execute("insert into sources(name,kind,enabled,priority,items) values('synthetic','playlist',1,1,?)",(service.encode(items),))
 def capture(label):
  with service.db() as c:
   channels=service.catalog(c);cfg=service.config(c)
   snapshots.append({'label':label,'channels':channels,'exports':{k:export(k,channels,'http://fixture.invalid/guide.xml') for k in ('m3u','txt','json')}})
 capture('initial')
 for criteria,speed,height in [('both',100,720),('speed',1000,720),('resolution',100,1080),('both',50,480)]:
  service.edit('settings',dict(hours=0,mode='merge',strict=True,auto_probe=False,probe_mode='pipeline',criteria=criteria,min_speed=speed,min_height=height));capture(f'settings:{criteria}:{speed}:{height}')
 for mode in ('off','on','auto'):
  service.edit('route',dict(name='ALPHA',url=items[0]['url'],mode=mode));capture('manual:'+mode)
 for speed,height in ((100,720),(100.001,720),(101,719),(101,720)):
  r=dict(method='continuous',quality='流畅',advanced=True,speed=speed,resolution={'height':height})
  snapshots.append({'boundary':[speed,height],'assessment':assess(r,dict(criteria='both',min_speed=100,min_height=720))})
 for code,note in ((429,'limited'),(607,'restricted'),(403,'Blocked by network policy'),(504,'timeout'),(404,'not found')):
  r=classify_result({'httpStatus':code,'note':[note]});snapshots.append({'error':code,'result':r,'assessment':assess(r,{})})
print(json.dumps(snapshots,ensure_ascii=False,sort_keys=True))

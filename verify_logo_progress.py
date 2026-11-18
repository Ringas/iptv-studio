"""台标进度独立、检测优先、去重、失败汇总、关闭取消和日志节流。"""
from pathlib import Path
from unittest.mock import patch
import tempfile,time
import service

def until(predicate):
 end=time.monotonic()+3
 while not predicate():
  assert time.monotonic()<end,'state did not arrive'
  time.sleep(.01)

with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
 service.init();events=[];calls=[];original=service.job.copy()
 with service.db() as c:
  items=[dict(name='fixture'+str(i),url=f'https://fixture.invalid/{i}',attrs={'tvg-logo':f'https://fixture.invalid/logo{j}.png'} if j is not None else {},options=[]) for i,j in enumerate([0,1,1,None])]
  c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode(items),))
  cfg=service.config(c);cfg.update(minutes=0,logo_beauty='light');service.save_config(c,cfg)
  before=c.execute('select count(*) from logs').fetchone()[0]
 def image(candidates,*_,**__):
  calls.append(candidates)
  if 'logo1.png' in candidates[0]:raise ValueError('fixture unavailable')
  return b'fixture'
 try:
  with patch.object(service,'cached_image',side_effect=image),patch.object(service,'event',side_effect=lambda *a,**k:events.append((a,k))):
   service.job.update(running=True,probing=True,done=2,total=9,message='probe untouched')
   probe=service.job.copy();service.request_logo_warm()
   until(lambda:service.logo_status()['phase']=='waiting')
   assert not calls and service.job==probe
   assert service.logo_status()['total']==2 and service.logo_status()['skipped']==1
   # Closing while waiting cancels without waiting for the detector to finish.
   with service.db() as c:cfg=service.config(c);cfg['logo_beauty']='off';service.save_config(c,cfg)
   service.request_logo_warm();worker=service._logo_warm_thread
   if worker:worker.join(3);assert not worker.is_alive()
   assert not calls and service.logo_status()['phase']=='off' and service.job==probe
   # A fresh queue stays pending until detection ends, then counts success/failure once per image.
   with service.db() as c:cfg['logo_beauty']='light';service.save_config(c,cfg)
   service.request_logo_warm();until(lambda:service.logo_status()['phase']=='waiting')
   service.job.update(running=False,probing=False);probe=service.job.copy();service._logo_warm_changed.set()
   until(lambda:service.logo_status()['phase']=='complete')
   state=service.logo_status();assert (state['done'],state['total'],state['ready'],state['failed'])==(2,2,1,1)
   assert service.job==probe and len(calls)==2
   assert any(a[0]=='logo.waiting' for a,k in events) and any(a[0]=='logo.resumed' for a,k in events)
   assert any(a[0]=='logo.finished' and '未能处理 1' in a[1] for a,k in events)
   with service.db() as c:assert c.execute('select count(*) from logs').fetchone()[0]==before,'frontend task logs changed'
 finally:
  service.job.clear();service.job.update(original);service._logo_warm_changed.set()
  worker=service._logo_warm_thread
  if worker:worker.join(3)
print('通过：检测期间等待、检测结束恢复、关闭取消、图片去重、无图跳过、成功/失败进度、后台日志与前台记录隔离。')

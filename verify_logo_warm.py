"""台标保存独立于测速；快速切换只继续处理最新模式。"""
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
import service

with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
 service.init()
 with service.db() as c:
  items=[dict(name='Fixture'+str(i),url=f'https://fixture.invalid/{i}.m3u8',attrs={'tvg-logo':f'https://fixture.invalid/{i}.png'},options=[]) for i in range(4)]
  c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode(items),))
  cfg=service.config(c);cfg.update(minutes=0);service.save_config(c,cfg)
 service.job.update(running=True,message='unrelated probe remains running',done=3,total=10)
 before=service.job.copy();entered=threading.Event();release=threading.Event();calls=[]
 def image(candidates,mode,*_,**__):
  calls.append(mode)
  if len(calls)==1:entered.set();assert release.wait(5)
  return b'fixture'
 def save(mode):
  with service.db() as c:cfg=service.config(c)
  service.edit('settings',{**cfg,'hours':0,'logo_beauty':mode})
 try:
  with patch.object(service,'cached_image',side_effect=image),patch.object(service,'probe',side_effect=AssertionError('style started probe')):
   save('light');assert entered.wait(5),'save did not start background work'
   worker=service._logo_warm_thread
   save('dark');save('light');save('off')
   assert worker is service._logo_warm_thread,'rapid saves created extra worker threads'
   release.set();worker.join(5);assert not worker.is_alive()
   assert calls==['light'],'superseded style continued preparing channels'
   with service.db() as c:assert service.config(c)['logo_beauty']=='off'
   assert service.job==before,'logo work changed the running detection task'
   save('dark');worker=service._logo_warm_thread
   if worker:worker.join(5)
   assert calls==['light']+['dark']*4
   save('dark');worker=service._logo_warm_thread
   if worker:worker.join(5)
   assert calls==['light']+['dark']*8,'saving an unchanged enabled mode did not begin preparation'
   save('transparent');worker=service._logo_warm_thread
   if worker:worker.join(5)
   assert calls==['light']+['dark']*8+['transparent']*4
   assert service.job==before
 finally:
  release.set()
  worker=service._logo_warm_thread
  if worker:worker.join(5)
print('通过：设置保存立即后台处理、同模式重存生效、快速切换取消旧队列、关闭停止后续处理、与正在运行的测速完全独立。')

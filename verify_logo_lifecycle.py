"""全局引用清理与独立周检调度；不使用真实数据或网络。"""
from pathlib import Path
from unittest.mock import patch
import tempfile
import service,logos

with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
 service.init();image=logos.png(20,12,bytes((30,70,130,255))*240)
 def join():
  worker=service._logo_warm_thread
  if worker:worker.join(10);assert not worker.is_alive()
 with patch.object(service,'fetch',return_value=(image,'https://fixture.invalid/image')):
  with service.db() as c:
   items=[dict(name=n,url=f'https://fixture.invalid/live/{i}',attrs={'tvg-logo':f'https://fixture.invalid/logo/{i}'},options=[]) for i,n in enumerate(['CCTV1','CGTN'])]
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode(items),));sid=c.execute('select id from sources').fetchone()[0]
   cfg=service.config(c);cfg.update(minutes=0,logo_beauty='dark');service.save_config(c,cfg)
  service.request_logo_warm();join();folder=Path(d)/'logo-cache'
  assert len(list(folder.glob('content-*.png')))==1
  service.edit('visibility',{'kind':'channel','name':'CCTV1综合','hide':True});join()
  assert len(list(folder.glob('content-*.png')))==1,'shared logo was deleted too early'
  service.edit('source-state',{'id':sid,'enabled':False});join()
  assert not list(folder.glob('content-*.png')) and not list((folder/'originals').glob('*.png')),'unused logos not pruned'
 # The periodic logo check still runs with subscription scheduling disabled.
 with patch.object(service,'request_logo_warm') as warm,patch.object(service,'logo_status',return_value={'running':False}),patch.object(service.time,'sleep',side_effect=[None,StopIteration]):
  try:service.scheduler()
  except StopIteration:pass
  warm.assert_called_once_with(only_missing=True,quiet=True)
 with patch.object(service,'request_logo_warm') as warm,patch.object(service,'logo_status',return_value={'running':True}),patch.object(service.time,'sleep',side_effect=[None,StopIteration]):
  try:service.scheduler()
  except StopIteration:pass
  warm.assert_not_called()
print('通过：不同地址同图去重、共享台标保留、隐藏/停用后无引用清理、关闭订阅定时仍独立周检、已有台标任务不被重复打断。')

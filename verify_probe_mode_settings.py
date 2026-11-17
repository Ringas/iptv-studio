"""Saved detection selection, environment precedence and scheduler dispatch."""
import tempfile,os,json
from pathlib import Path
from unittest.mock import patch
import service
from verify_dual_schedule import GOOD

def main():
 with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)),patch.dict(os.environ,{'IPTV_PROBE_WORKERS':'2'}):
  service.init()
  def read():
   with service.db() as c:return service.config(c)
  assert read()['probe_workers']==2 # legacy unsaved environment fallback
  payload=dict(hours=6,mode='merge',auto_probe=False,strict=True,criteria='speed',min_speed=1,min_height=180)
  for selected,workers in [('pipeline',1),('pipeline_dual',2),('pipeline',1)]:
   service.edit('settings',{**payload,'probe_mode':selected})
   cfg=read();assert cfg['probe_mode']=='pipeline' and cfg['probe_workers']==workers,cfg
   assert (cfg['criteria'],cfg['min_speed'],cfg['min_height'])==('speed',1,180)
   with patch.dict(os.environ,{'IPTV_PROBE_WORKERS':'1' if workers==2 else '2'}):assert read()['probe_workers']==workers
   first=[]
   def prefetch(urls,*args):first.append(True);return {u:(dict(GOOD),0,False) for u in urls}
   with patch.object(service,'first_pass',side_effect=prefetch),patch.object(service,'probe',return_value=dict(GOOD)),patch.object(service,'gateway_delay',return_value=0):
    service.job.update(running=True,stop_queue=False,stop_line=False);service.run('probe',['http://fixture.test/a'])
   assert bool(first)==(workers==2),(selected,service.job)
   assert '任务中断' not in service.job['message'],service.job
  before=read()
  for invalid in ['pipeline_triple','',None]:
   try:service.edit('settings',{**payload,'probe_mode':invalid})
   except ValueError:pass
   else:raise AssertionError('invalid mode accepted')
   assert read()==before
  for selected in ['adaptive','continuous','legacy']:
   service.edit('settings',{**payload,'probe_mode':selected});assert read()['probe_mode']==selected and read()['probe_workers']==1
  # All three visible strategies use the same saved numerical thresholds.
  for selected,expected in [('adaptive','quick'),('pipeline_dual','pipeline'),('continuous','continuous')]:
   service.edit('settings',{**payload,'probe_mode':selected})
   methods=[]
   def sample(url,method,*args):
    methods.append(method);return dict(GOOD)
   def dual_sample(urls,*args):
    methods.append('pipeline');return {u:(dict(GOOD),0,False) for u in urls}
   with patch.object(service,'probe',side_effect=sample),patch.object(service,'first_pass',side_effect=dual_sample),patch.object(service,'gateway_delay',return_value=0):
    service.job.update(running=True,stop_queue=False,stop_line=False)
    service.run('probe',['http://fixture.test/a'])
   assert methods==[expected],(selected,methods)
   assert service.job['probe_policy']==dict(criteria='speed',min_speed=1,min_height=180)
  import re
  html=(Path(__file__).parent/'index.html').read_text()
  select=re.search(r'<select id="probeMode"[^>]*>(.*?)</select>',html).group(1)
  assert re.findall(r'<option value="([^"]+)">([^<]+)</option>',select)==[
   ('adaptive','快速检测'),('pipeline_dual','均衡检测'),('continuous','深度检测')]

 print('PASS: serial/dual save+reload, UI choice wins over environment, saved configurable thresholds, real dispatch selection, legacy modes and invalid input.')
if __name__=='__main__':main()

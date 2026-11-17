"""Primary selection uses shared catalog order without mutating manual control."""
import copy,tempfile
from pathlib import Path
from unittest.mock import patch
import service
from catalog import quality_order,selected
from verify_qualification_views import record

def main():
 def line(url,**changes):
  test=record();test.update(quality='流畅',startup=1,direct_history=[1]*3,recent=[1]*3,stalls=0);test.update(changes)
  return dict(url=url,priority=1,assessment='保留',test=test)
 slow=line('slow',speed=200,startup=.1)
 fast=line('fast',speed=900,startup=5)
 unstable=line('unstable',speed=9999,quality='可播但不稳')
 hd=line('hd',speed=300,resolution={'width':1920,'height':1080})
 for criteria in ('speed','resolution','both'):
  order=lambda r:quality_order(r,criteria)
  assert order(fast)<order(slow)<order(unstable)
  assert order(hd)<order(fast)
  # CCTV1 regression: both clean 1080p, one versus two successes.
  cctv_fast=line('cctv-fast',speed=29685.82,startup=.72,direct_history=[1],recent=[1],resolution={'height':1080})
  cctv_slow=line('cctv-slow',speed=4361.91,startup=1.86,direct_history=[1,1],recent=[1,1],resolution={'height':1080})
  assert order(cctv_fast)<order(cctv_slow)
  for n in (0,1,2,10):
   assert order(line('clean-fast',speed=900,direct_history=[1]*n,recent=[1]*n))<order(slow)
  assert order(line('recent-failure',speed=9999,recent=[1,0]))>order(slow)
  for opening in ('重连后可播','复核后可播','复测后可播'):
   assert order(line('recovered',speed=9999,opening=opening))>order(slow)
  assert order(line('intermittent',speed=9999,intermittent=True))>order(slow)

  assert order(line('retry',speed=9999,direct_history=[0,1,0]))>order(slow)
  assert order(line('stalls',speed=9999,stalls=2))>order(slow)
  assert order(line('unknown',speed=None,resolution={},startup=None))>order(fast)
 with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
  service.init()
  rows=[slow,fast,unstable,hd]
  urls={r['url']:'http://fixture.invalid/'+r['url'] for r in rows}
  with service.db() as c:
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name='Fixture',url=urls[r['url']],attrs={},options=[]) for r in rows]),))
   for r in rows:c.execute('insert into tests values(?,?)',(urls[r['url']],service.encode(r['test'])))
   cfg=service.config(c)
   channel=service.catalog(c)[0]
   assert channel['lines'][0]['url']==urls['hd']
   assert next(selected([channel],{**cfg,'mode':'best'}))['lines'][0]['url']==urls['hd']
   c.execute('insert into route_modes values(?,?,?)',('FIXTURE',urls['hd'],'off'))
   channel=service.catalog(c)[0]
   assert channel['lines'][0]['url']==urls['fast']
   assert next(selected([channel],{**cfg,'mode':'merge'}))['lines'][0]['url']==urls['fast']
   assert dict(c.execute('select url,mode from route_modes'))=={urls['hd']:'off'}
   # A new completed result reorders the catalog immediately, without another setting.
   improved=copy.deepcopy(slow['test']);improved['speed']=1800
   c.execute('update tests set result=? where url=?',(service.encode(improved),urls['slow']))
   assert service.catalog(c)[0]['lines'][0]['url']==urls['slow']
 print('PASS: stability, resolution, speed, startup, unknown metrics, manual-off exclusion, updated results and shared export order.')
if __name__=='__main__':main()

"""Real local HLS/FFmpeg: an isolated stall needs a full fresh clean window."""
import os,json,time,subprocess,threading,tempfile
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import service
from verify_probe_schedule import exercise,GOOD
ROOT=Path(__file__).parent
ff=os.environ['FFMPEG_BIN']
def clip(size):return subprocess.check_output([ff,'-v','error','-f','lavfi','-i',f'color=c=blue:s={size}:r=12','-t','1','-c:v','libx264','-g','12','-f','mpegts','pipe:1'])
padding=(bytes([0x47,0x1f,0xff,0x10])+b'\xff'*184)*1024
high,low=clip('1280x720')+padding,clip('320x180')+padding;opened={};counts=Counter();lock=threading.Lock()
class Fixture(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  case,leaf=self.path.strip('/').split('/')
  with lock:counts[self.path]+=1
  if leaf=='entry':
   opened[case]=time.monotonic();self.send_response(302);self.send_header('Set-Cookie',f'case={case}; Path=/{case}');self.send_header('Location',f'/{case}/live');self.end_headers();return
  if self.headers.get('Cookie')!=f'case={case}':self.send_error(403);return
  if leaf=='live':
   seq=int((time.monotonic()-opened[case])//2)*2
   if case=='frozen':seq=min(seq,2)
   body=(f'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:{seq}\n'+''.join(f'#EXTINF:1,\n/{case}/{i}.ts\n' for i in range(seq,seq+4))).encode()
  else:
   index=int(leaf.split('.')[0])
   if index==4:time.sleep(.65)
   if case=='persistent' and index==6:time.sleep(2)
   if case=='limited' and index==6:
    self.send_response(429);self.send_header('Retry-After','12');self.end_headers();return
   body=low if case=='low' and index>=6 else b'not video' if case=='corrupt' and index>=6 else high
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
  try:self.wfile.write(body)
  except OSError:pass
with ThreadingHTTPServer(('127.0.0.1',0),Fixture) as server:
 threading.Thread(target=server.serve_forever,daemon=True).start()
 def run(case):
  raw=subprocess.check_output(['node',str(ROOT/'probe.mjs'),f'http://127.0.0.1:{server.server_port}/{case}/entry','--pipeline'],env={**os.environ,'IPTV_PROBE_MANAGED':'1','IPTV_PROBE_RECHECK':'0','IPTV_PROBE_MIN_HEIGHT':'720'},timeout=80)
  return case,json.loads(raw.splitlines()[-1])
 with ThreadPoolExecutor(3) as pool:results=dict(pool.map(run,['recovered','persistent','frozen','limited','low','corrupt']))
 r=results['recovered'];assert r['quality']=='流畅' and r['advanced'],r
 recovery=r['recovery'];assert recovery['state']=='passed' and recovery['seconds']>=recovery['required_seconds']==24,recovery
 assert recovery['initial_observation']['stalls']==1 and 0<recovery['initial_observation']['stall_seconds']<=1,recovery
 assert recovery['decode']['frames']>=3 and recovery['decode']['pts_advanced'],recovery
 assert r['speed']>100 and r['resolution']['height']==720 and r['stalls']==0 and r['segments']>=2,r
 assert counts['/recovered/entry']==1 and r.get('entry_retries',0)==0,counts
 assert max(n for path,n in counts.items() if path.startswith('/recovered/') and path.endswith('.ts'))==1,counts
 assert results['persistent']['quality']=='可播但不稳' and results['persistent'].get('recovery',{}).get('state')=='failed',results['persistent']
 assert results['frozen']['quality']!='流畅',results['frozen']
 assert results['limited']['httpStatus']==429 and results['limited']['retryAfter']>=12,results['limited']
 assert results['low']['resolution']['height']==180 and results['low']['coarse_rejected'],results['low']
 assert results['corrupt']['quality']=='未能验证',results['corrupt']
 cfg=service.config
 with patch.object(service,'config',side_effect=lambda c:{**cfg(c),'probe_mode':'pipeline'}):
  calls,saved,_,_=exercise(['https://fixture.test/live'],lambda *_:r.copy())
  assert len(calls)==1 and saved['https://fixture.test/live']['qualification_history']==[1],saved
  assert service.needs_recheck(results['persistent'])
 print('PASS: same cookie/session, no duplicate media, full 24s clean window, fresh multiframe/PTS/live evidence and strict thresholds, one history; persistent/frozen/429/low/corrupt never recover as qualified')
 print('Recovered observation:',json.dumps({k:r[k] for k in ['seconds','stalls','stall_seconds','speed','recovery']},ensure_ascii=False))
 server.shutdown()

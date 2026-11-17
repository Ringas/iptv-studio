"""Real HLS/FFmpeg regression: reuse session without weakening continuous checks."""
import os,json,time,subprocess,threading,tempfile
from collections import Counter
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parent
ff=os.getenv('FFMPEG_BIN','ffmpeg')
clip=subprocess.check_output([ff,'-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=12','-t','1','-c:v','libx264','-g','12','-f','mpegts','pipe:1'])
lowclip=subprocess.check_output([ff,'-v','error','-f','lavfi','-i','color=c=blue:s=320x180:r=12','-t','1','-c:v','libx264','-f','mpegts','pipe:1'])
counts=Counter();starts={};lock=threading.Lock()
class Fixture(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  parts=self.path.strip('/').split('/');case=parts[0]
  with lock:counts[self.path]+=1;starts.setdefault(case,time.monotonic())
  if self.headers.get('Referer')!='https://fixture.invalid/':self.send_error(403);return
  if parts[-1]=='entry':
   self.send_response(302);self.send_header('Set-Cookie','session='+case+'; Path=/'+case);self.send_header('Location','/'+case+'/live');self.end_headers();return
  if self.headers.get('Cookie')!='session='+case:self.send_error(403);return
  if parts[-1]=='live':
   seq=0 if case=='frozen' else int((time.monotonic()-starts[case])//2)
   rows=''.join(f'#EXTINF:2,\n/{case}/{i}.ts\n' for i in range(seq,seq+4))
   body=f'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:{seq}\n{rows}'.encode()
  elif case=='limited' and int(parts[-1].split('.')[0])>=4:
   self.send_response(429);self.send_header('Retry-After','8');self.end_headers();return
  else:body=lowclip if case=='low' else clip
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
with ThreadingHTTPServer(('127.0.0.1',0),Fixture) as server:
 threading.Thread(target=server.serve_forever,daemon=True).start()
 def run(case,mode):
  env={**os.environ,'IPTV_PROBE_MANAGED':'1','IPTV_PROBE_MIN_HEIGHT':'720','IPTV_PROBE_OPTIONS':json.dumps(['#EXTVLCOPT:http-referrer=https://fixture.invalid/'])}
  t=time.monotonic();raw=subprocess.check_output(['node',str(ROOT/'probe.mjs'),f'http://127.0.0.1:{server.server_port}/{case}/entry','--'+mode],env=env,timeout=90)
  return json.loads(raw.splitlines()[-1]),time.monotonic()-t
 def separate():
  q,qt=run('separate','quick');r,rt=run('separate','continuous');return q,r,qt+rt
 with ThreadPoolExecutor(3) as pool:
  a=pool.submit(separate);b=pool.submit(run,'shared','pipeline');c=pool.submit(run,'frozen','pipeline')
  q,old,oldtime=a.result();new,newtime=b.result();frozen,_=c.result()
 for r in (old,new):
  assert r['quality']=='流畅' and r['advanced'],r
  assert r['evidence']['decode']['frames']>=3 and r['evidence']['decode']['pts_advanced'],r
  assert r['seconds']>=24,r # the whole continuous observation window must elapse
 assert new['handoff']['session_reused'] and new['handoff']['reused_bytes']>0,new
 assert new['handoff']['reused_decoded_frames']>=3,new
 assert new['handoff']['initial_sequence'] is not None,new
 assert counts['/shared/entry']==1,counts
 shared=[n for path,n in counts.items() if path.startswith('/shared/') and path.endswith('.ts')]
 separate=[n for path,n in counts.items() if path.startswith('/separate/') and path.endswith('.ts')]
 assert max(shared)==1,counts
 assert max(separate)>1,counts
 assert counts['/separate/entry']==2,counts
 assert frozen['quality'] in ('暂时失败','未能验证') and not frozen.get('advanced'),frozen
 assert not new.get('entry_retries'),new
 low,_=run('low','pipeline')
 assert low['coarse_rejected'] and low['resolution']['height']==180,low
 assert sum(n for path,n in counts.items() if path.startswith('/low/') and path.endswith('.ts'))==1,counts
 limited,_=run('limited','pipeline')
 assert limited['httpStatus']==429 and limited['retryAfter']>=8,limited
 assert limited['quality']=='未能验证',limited
 print('PASS: one entry/cookie session, no repeated segments, reused multiframe evidence/sequence, continuous duration unchanged, frozen stream rejected')
 print('Fixture comparison:',json.dumps({'separate_seconds':round(oldtime,2),'shared_seconds':round(newtime,2),'separate_media_requests':sum(separate),'shared_media_requests':sum(shared)}))
 server.shutdown()

"""Actual timeout -> cooled fresh-entry recovery, isolated local HLS and FFmpeg."""
import json,os,subprocess,tempfile,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import service
from catalog import classify_result,assess
from verify_probe_schedule import exercise,GOOD

# Mock-clock checks: no new retry budget, unknown stays unknown, prior state survives.
original=service.config
with patch.object(service,'config',side_effect=lambda c:{**original(c),'probe_mode':'pipeline'}):
 for failure in [dict(quality='未能验证',retryable=True,note=['请求超时'],retry_deferred='entry_cooldown'),
                 dict(httpStatus=429,retryAfter=7,note=['limited']),
                 dict(httpStatus=607,note=['network policy']),
                 dict(httpStatus=403,note=['Blocked by network policy'])]:
  calls,saved,modes,_=exercise(['https://fixture.test/live'],lambda *_:failure.copy(),seed=True)
  assert len(calls)==2 and calls[1][2]-calls[0][2]>=7,calls
  prior=saved['https://fixture.test/live'];assert prior['speed']==GOOD['speed'] and modes[0]['mode']=='off',prior
  assert prior['last_attempt']['inconclusive'],prior

clip=subprocess.check_output([os.environ['FFMPEG_BIN'],'-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=12','-t','1','-c:v','libx264','-g','12','-f','mpegts','pipe:1'])
entries=[];media=[];opened=0.;cooled=[]
class Fixture(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  global opened
  if self.path=='/entry':
   entries.append(time.monotonic());opened=entries[-1]
   self.send_response(302);self.send_header('Location',f'/session{len(entries)}/live');self.end_headers();return
  if self.path.endswith('/live'):
   seq=int((time.monotonic()-opened)//2)
   root=self.path.rsplit('/',1)[0]
   body=('#EXTM3U\n#EXT-X-TARGETDURATION:2\n'+f'#EXT-X-MEDIA-SEQUENCE:{seq}\n'+''.join(f'#EXTINF:2,\n{root}/{i}.ts\n' for i in range(seq,seq+4))).encode()
  else:
   media.append((self.path,time.monotonic()))
   if self.path.startswith('/session1/'):
    # A body timeout, not merely a delay before HTTP headers.
    self.send_response(200);self.send_header('Content-Length','9999999');self.end_headers()
    try:self.wfile.write(clip[:188]);self.wfile.flush();time.sleep(11)
    except OSError:pass
    return
   body=clip
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
  try:self.wfile.write(body)
  except OSError:pass
with ThreadingHTTPServer(('127.0.0.1',0),Fixture) as server:
 threading.Thread(target=server.serve_forever,daemon=True).start()
 with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
  service.init();url=f'http://127.0.0.1:{server.server_port}/entry'
  with service.db() as c:
   cfg=service.config(c);cfg.update(probe_mode='pipeline',criteria='both',min_speed=100,min_height=720);service.save_config(c,cfg)
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name='CCTV1',url=url,attrs={},options=[])]),))
  service.job.update(stop_queue=False,stop_line=False,running=True);service.gateway_windows.clear();service.gateway_limits.clear()
  call=service.probe;attempts=[]
  def probe(*args):
   r=call(*args);attempts.append(r);cooled.append(time.monotonic());return r
  with patch.object(service,'probe',side_effect=probe):service.run('probe',[url])
  assert len(entries)==len(attempts)==2,(entries,attempts)
  assert len([x for x,_ in media if x.startswith('/session1/')])==1,media
  first=classify_result(attempts[0]);assert first['error_category']=='timeout' and first['inconclusive'],first
  assert first['retry_deferred']=='entry_cooldown',first
  assert entries[1]-cooled[0]>=7.9,(entries,cooled)
  r=attempts[1];assert r['quality']=='流畅' and r['advanced'] and r['resolution']['height']==720,r
  assert r['seconds']>=20 and r['evidence']['decode']['frames']>=3 and r['evidence']['decode']['pts_advanced'],r
  with service.db() as c:
   saved=json.loads(c.execute('select result from tests where url=?',(url,)).fetchone()[0])
  assert assess(saved,cfg)=='保留' and saved['entry_retries']==1 and saved['qualification_history']==[1],saved
  print('PASS: one media timeout per entry, actual 10s body timeout, >=8s cooled fresh-entry retry, full continuous multiframe/live checks, one independent history; 429/policy/timeout preserve prior state')
 server.shutdown()

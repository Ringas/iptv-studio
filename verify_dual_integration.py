"""Actual service + two Node workers + HLS + FFmpeg + isolated low-height recheck."""
import json,os,subprocess,threading,tempfile,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import service
ff=os.getenv('FFMPEG_BIN','ffmpeg')
def clip(size):return subprocess.check_output([ff,'-v','error','-f','lavfi','-i',f'testsrc2=size={size}:rate=12','-t','1','-c:v','libx264','-preset','ultrafast','-g','12','-f','mpegts','pipe:1'])
high=clip('1280x720');low=clip('320x180');starts={};guard=threading.Lock()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*_):pass
 def do_GET(self):
  kind=self.path.split('/')[1]
  with guard:starts.setdefault(kind,time.monotonic());elapsed=time.monotonic()-starts[kind]
  if self.path.endswith('m3u8'):
   seq=int(elapsed//2);body=('#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:'+str(seq)+'\n'+''.join(f'#EXTINF:2,\n/{kind}/{n}.ts\n' for n in range(seq,seq+4))).encode()
  else:body=low if kind=='low' else high
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)),patch.dict(os.environ,{'IPTV_PROBE_WORKERS':'2'}):
 service.init();service.gateway_windows.clear();service.gateway_limits.clear()
 urls=[f'http://127.0.0.1:{server.server_port}/a/live.m3u8',f'http://localhost:{server.server_port}/b/live.m3u8',f'http://127.0.0.1:{server.server_port}/low/live.m3u8']
 with service.db() as c:
  cfg=service.config(c);cfg.update(probe_mode='pipeline',criteria='both',min_height=720,min_speed=100);service.save_config(c,cfg)
  c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode([dict(name=str(i),url=u,attrs={},options=[]) for i,u in enumerate(urls)]),))
 original=service.probe;calls=[]
 def probe(u,m,o):
  start=time.monotonic();r=original(u,m,o)
  with guard:calls.append(dict(url=u,method=m,start=start,end=time.monotonic(),result=r))
  return r
 service.job.update(running=True,run_id='integration',stop_queue=False,stop_line=False,active_lines={})
 with patch.object(service,'probe',side_effect=probe):service.run('probe',urls)
 assert '任务中断' not in service.job['message'],service.job
 initial=[x for x in calls if x['method']=='pipeline'];rechecks=[x for x in calls if x['method']=='continuous']
 assert len(initial)==3 and len(rechecks)==1,calls
 good=[x for x in initial if x['url'] in urls[:2]]
 assert min(x['end'] for x in good)-max(x['start'] for x in good)>20
 for x in good:
  r=x['result'];assert r['quality']=='流畅' and r['seconds']>=24 and r['advanced'] and r['speed']>100,r
  assert r['evidence']['decode']['frames']>=3 and r['evidence']['decode']['pts_advanced'] and r['resolution']['height']==720,r
 assert rechecks[0]['start']>=max(x['end'] for x in initial)
 assert rechecks[0]['result']['seconds']>=24 and rechecks[0]['result']['resolution']['height']==180
 with service.db() as c:tests={r['url']:json.loads(r['result']) for r in c.execute('select * from tests')}
 assert tests[urls[2]]['qualification_history']==[0] and not tests[urls[2]]['qualification_pending'],tests[urls[2]]
 assert service.job['message']=='测速完成 · 2 条通过 · 1 条未通过',service.job
 # Exercise actual process cancellation, not just mocked worker flags.
 service.gateway_windows.clear();service.gateway_limits.clear()
 service.job.update(running=True,run_id='cancel',stop_queue=False,stop_line=False,active_lines={})
 before=tests.copy();thread=threading.Thread(target=service.run,args=('probe',urls[:2]));thread.start()
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  with service.LOCK:states=list(service.active_states.values());ready=len(states)==2 and all(x['process'] is not None for x in states)
  if ready:break
  time.sleep(.02)
 assert ready,'two real child processes did not start'
 processes=[x['process'] for x in states]
 service.stop_probe('cancel',urls[0])
 deadline=time.monotonic()+3
 while processes[0].poll() is None and time.monotonic()<deadline:time.sleep(.02)
 assert processes[0].poll() is not None and processes[1].poll() is None
 service.stop_probe('cancel');thread.join(5)
 assert not thread.is_alive() and all(x.poll() is not None for x in processes)
 with service.db() as c:after={r['url']:json.loads(r['result']) for r in c.execute('select * from tests')}
 assert before==after,'cancellation changed recorded measurements'
 assert not service.active_states and service.dual_lease_dir is None
 print('PASS: actual dual workers overlap >20s, both full24s/multiframe/PTS/live/speed/720, low height isolated full24s, one independent history, actual skip/stop kills only intended processes, leases clean.')
server.shutdown()

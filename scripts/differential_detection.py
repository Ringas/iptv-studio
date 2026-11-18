"""Compare real detector results from published images against fixed local media."""
import hashlib,json,math,os,ssl,subprocess,sys,threading,time,uuid
from collections import Counter
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
images={'baseline':'talktoo/iptv-studio:v1.1.3','candidate':'talktoo/iptv-studio:slim-test-55f8cf7'}
for image in images.values():subprocess.run(['docker','pull',image],check=True)
metadata={label:json.loads(subprocess.check_output(['docker','image','inspect',image]))[0] for label,image in images.items()}
media={};hashes={}
for label,size in [('good720','1280x720'),('good1080','1920x1080'),('low480','640x480')]:
 folder=out/label;folder.mkdir()
 subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c=blue:s={size}:r=12','-t','120','-c:v','libx264','-preset','ultrafast','-g','24','-sc_threshold','0','-f','hls','-hls_time','2','-hls_list_size','0','-hls_segment_filename',str(folder/'%03d.ts'),str(folder/'index.m3u8')],check=True)
 padding=(bytes([0x47,0x1f,0xff,0x10])+b'\xff'*184)*1536
 media[label]=[p.read_bytes()+padding for p in sorted(folder.glob('*.ts'))]
 hashes[label]={'segments':len(media[label]),'sha256':hashlib.sha256(b''.join(media[label])).hexdigest()}
starts={};counts=Counter();lock=threading.Lock()
class Fixture(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  case,run,leaf=urlsplit(self.path).path.strip('/').split('/')
  with lock:
   counts[run+':'+leaf]+=1;start=starts.setdefault(run,time.monotonic())
  if case=='timeout':time.sleep(15)
  status={'rate429':429,'blocked607':607,'policy403':403,'notfound':404}.get(case,200)
  if status!=200:
   body=b'Blocked by network policy' if case=='policy403' else b'fixture error'
  elif leaf=='entry':
   seq=0 if case=='frozen' else int((time.monotonic()-start)//2)
   body=(f'#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:{seq}\n'+''.join(f'#EXTINF:2,\n{i}.ts\n' for i in range(seq,seq+4))).encode()
  elif case=='corrupt':body=b'not video data'*100
  else:
   key=case if case in media else 'good720';body=media[key][int(leaf.split('.')[0])%len(media[key])]
  if case=='lowrate' and leaf!='entry':body=body[:-len(padding)]+padding[:188*256]
  self.send_response(status);self.send_header('Content-Length',str(len(body)))
  self.send_header('Content-Type','application/vnd.apple.mpegurl' if leaf=='entry' and status==200 else 'video/mp2t')
  if status==429:self.send_header('Retry-After','2')
  try:
   self.end_headers()
   if case in ('slow','lowrate') and leaf!='entry':
    for pos in range(0,len(body),4096):self.wfile.write(body[pos:pos+4096]);self.wfile.flush();time.sleep(.08)
   else:self.wfile.write(body)
  except OSError:pass
server=ThreadingHTTPServer(('127.0.0.1',18890),Fixture)
threading.Thread(target=server.serve_forever,daemon=True).start()
subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-keyout',str(out/'fixture-key.pem'),'-out',str(out/'fixture-ca.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(out/'fixture-ca.pem',out/'fixture-key.pem')
tls_server=ThreadingHTTPServer(('127.0.0.1',18891),Fixture);tls_server.socket=context.wrap_socket(tls_server.socket,server_side=True)
threading.Thread(target=tls_server.serve_forever,daemon=True).start()
code='''import json,sys,service
from catalog import classify_result,assess
mode=sys.argv[2];policy=dict(criteria="both",min_speed=100,min_height=720)
service.job.update(probe_policy=policy,pipeline=mode=="pipeline",rechecking=False,stop_line=False,stop_queue=False)
r=classify_result(service.probe(sys.argv[1],mode,[]));r["assessment"]=assess(r,policy)
print(json.dumps(r,ensure_ascii=False))'''
fields=('assessment','quality','httpStatus','error_category','inconclusive','advanced','pipeline','retryable','coarse_rejected')
def fingerprint(r):
 evidence=r.get('evidence',{})
 return {**{k:r.get(k) for k in fields},'height':(r.get('resolution') or {}).get('height'),'speed_above_100':r.get('speed')>100 if r.get('speed') is not None else None,'decode_state':evidence.get('decode',{}).get('state'),'pts_advanced':evidence.get('decode',{}).get('pts_advanced'),'live_state':evidence.get('live',{}).get('state')}
def validate(case,mode,r):
 if case in ('good720','good1080','tls720'):
  assert r['assessment']=='保留',(case,mode,r)
  assert r['speed']>100 and r['resolution']['height']==(1080 if case=='good1080' else 720),r
  if mode!='quick':assert r.get('advanced') and r['evidence']['decode']['pts_advanced'],r
 if case=='low480':assert r['assessment']!='保留' and r['resolution']['height']==480,r
 if case in ('rate429','blocked607','policy403','timeout'):
  assert r['assessment']=='未能验证' and r.get('inconclusive'),r
  assert r['error_category']=={'rate429':'rate_limited','blocked607':'network_restricted','policy403':'network_restricted','timeout':'timeout'}[case],r
 if case in ('notfound','corrupt','slow'):assert r['assessment']!='保留',r
 if case=='slow':assert r.get('speed',0)<=100,r
 if case=='lowrate':assert 0<r.get('speed',0)<=100 and r['assessment']=='速率未达标',r
 if case=='frozen':
  assert not r.get('advanced') and r.get('quality')!='流畅',r
  if mode=='pipeline':assert r['assessment']!='保留',r
cases=[(case,mode) for mode in ('quick','pipeline','continuous') for case in ('good720','good1080','low480','rate429','blocked607','policy403','notfound','corrupt')]+[('slow','quick'),('timeout','quick'),('frozen','pipeline'),('frozen','continuous'),('tls720','quick'),('tls720','continuous')]+[('lowrate',mode) for mode in ('quick','pipeline','continuous')]
if os.getenv('CASES_FILTER'):cases=[pair for pair in cases if pair[0] in os.environ['CASES_FILTER'].split(',')]
assert cases,'没有匹配的测试样本'
report={'images':images,'image_ids':{label:{'id':info['Id'],'repo_digests':info.get('RepoDigests',[])} for label,info in metadata.items()},'policy':{'criteria':'both','min_speed':100,'min_height':720},'fixture_sha256':hashes,'pairs':[],'differences':[],'validation_errors':[]}
try:
 for index,(case,mode) in enumerate(cases):
  pair={'case':case,'mode':mode,'order':['baseline','candidate'] if index%2==0 else ['candidate','baseline'],'results':{}}
  for label in pair['order']:
   run=uuid.uuid4().hex;url=f'{"https://localhost:18891" if case=="tls720" else "http://localhost:18890"}/{case}/{run}/entry';started=time.monotonic()
   proc=subprocess.run(['docker','run','--init','--rm','--network','host','--entrypoint','python3','--mount',f'type=bind,source={out}/fixture-ca.pem,target=/tmp/fixture-ca.pem,readonly','-e','NODE_EXTRA_CA_CERTS=/tmp/fixture-ca.pem','-e','IPTV_LOG_LEVEL=error',images[label],'-c',code,url,mode],capture_output=True,timeout=140)
   if proc.returncode:raise RuntimeError(proc.stderr.decode(errors='replace'))
   r=json.loads(proc.stdout.decode().splitlines()[-1]);elapsed=round(time.monotonic()-started,3)
   pair['results'][label]={'raw':r,'fingerprint':fingerprint(r),'wall_seconds':elapsed,'fixture_requests':{k.split(':',1)[1]:v for k,v in counts.items() if k.startswith(run+':')}}
   try:validate(case,mode,r)
   except (AssertionError,KeyError) as error:report['validation_errors'].append({'case':case,'mode':mode,'label':label,'error':str(error)})
   print(json.dumps({'case':case,'mode':mode,'label':label,'seconds':elapsed,'result':fingerprint(r)},ensure_ascii=False),flush=True)
  if pair['results']['baseline']['fingerprint']!=pair['results']['candidate']['fingerprint']:report['differences'].append({'case':case,'mode':mode})
  report['pairs'].append(pair);(out/'differential.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
finally:server.shutdown();server.server_close();tls_server.shutdown();tls_server.server_close()
print(json.dumps({'pairs':len(report['pairs']),'differences':report['differences'],'validation_errors':len(report['validation_errors'])},ensure_ascii=False),flush=True)
if report['differences'] or report['validation_errors']:raise SystemExit(1)

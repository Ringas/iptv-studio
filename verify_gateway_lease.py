"""Actual Node HTTP redirects: colliding sessions never reach the shared target."""
import hashlib,json,os,subprocess,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from dual_probe import release_leases,store_cooldown
from unittest.mock import patch
import service
ROOT=Path(__file__).parent
class Handler(BaseHTTPRequestHandler):
 hits=0
 def log_message(self,*_):pass
 def do_GET(self):
  if self.path=='/limit':
   self.send_response(429);self.send_header('Retry-After','6');self.end_headers();self.wfile.write('等待 20 秒后重试'.encode());return
  if self.path=='/redirect':
   self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{self.server.server_port}/target');self.end_headers();return
  type(self).hits+=1;self.send_response(200);self.end_headers();self.wfile.write(b'ok')
def main():
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 with tempfile.TemporaryDirectory() as d:
  def env(owner):return {**os.environ,'IPTV_GATEWAY_LEASE_DIR':d,'IPTV_GATEWAY_OWNER':owner}
  code="import {mediaFetch} from './media-http.mjs'; const r=await mediaFetch(process.argv[1]); await r.text(); console.log('ready'); await new Promise(r=>setTimeout(r,10000));"
  holder=subprocess.Popen(['node','--input-type=module','-e',code,f'http://127.0.0.1:{server.server_port}/target'],cwd=ROOT,env=env('holder'),stdout=subprocess.PIPE,text=True)
  try:
   while holder.stdout.readline().strip()!='ready':
    assert holder.poll() is None,'holder exited before ready'
   retry="import {mediaFetch} from './media-http.mjs';try{const r=await mediaFetch(process.argv[1]);await r.text();console.log('passed')}catch(e){console.log(JSON.stringify({deferred:e.retryDeferred,status:e.httpStatus,retryAfter:e.retryAfter,unverified:e.unverified}))}"
   def run(url):return subprocess.check_output(['node','--input-type=module','-e',retry,url],cwd=ROOT,env=env('other'),text=True).strip().splitlines()[-1]
   r=json.loads(run(f'http://localhost:{server.server_port}/redirect'))
   assert r['deferred']=='gateway_isolation' and r['unverified'] and Handler.hits==1,r
   release_leases(d,'other')
  finally:
   holder.kill();holder.wait();release_leases(d,'holder')
  assert run(f'http://localhost:{server.server_port}/redirect')=='passed' and Handler.hits==2
  release_leases(d,'other')
  store_cooldown(d,'127.0.0.1',60)
  r=json.loads(run(f'http://localhost:{server.server_port}/redirect'))
  assert r['status']==429 and r['retryAfter']>=59 and Handler.hits==2
  release_leases(d,'other')
  assert not list(Path(d).glob('*.lease'))
 with tempfile.TemporaryDirectory() as d,patch.object(service,'dual_lease_dir',d):
  service.job.update(stop_queue=False,stop_line=False,pipeline=True,rechecking=False)
  service.gateway_windows.clear();service.gateway_limits.clear()
  result=service.probe(f'http://127.0.0.1:{server.server_port}/limit','pipeline',[])
  assert result['httpStatus']==429 and result['retryAfter']==21,result
  assert service.gateway_delay('https://127.0.0.1')>=20
  assert service.gateway_limits['127.0.0.1'][0]==1,'body enrichment counted as a second 429'
  before=Handler.hits
  result=service.probe(f'http://127.0.0.1:{server.server_port}/target','continuous',[])
  assert result['httpStatus']==429 and result['retryAfter']>=20 and Handler.hits==before,result
 server.shutdown()
 print('PASS: redirected gateway collision blocks HTTP, killed owner cleanup, isolated fresh fetch, shared cooldown blocks target, longer body cooldown extends header without double-counting 429.')
if __name__=='__main__':main()

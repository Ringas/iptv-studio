"""Wrapped errors retain their cause, respect cooldown and never overwrite manual state."""
import json,os,subprocess,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import webplay
from catalog import classify_result,assess
from verify_probe_schedule import exercise,GOOD
root=Path(__file__).parent
fields=('httpStatus','retryAfter','upstreamCooldown','retryable')
for row in json.loads((root/'fixtures/upstream-errors.json').read_text()):
 value=webplay.response_failure(row['body'],row['headers'],row['stage'],row.get('httpStatus'))
 assert ({k:value.get(k) for k in fields} if value else None)==row['expected'],(row['name'],value)
 if row['name']=='redacted message':assert 'secret-value' not in value['error'] and 'sign=private' not in value['error']
with patch.object(webplay.time,'time',return_value=1767225600):
 assert webplay.response_failure('HTTP 503',{'retry-after':'Thu, 01 Jan 2026 00:00:35 GMT'})['retryAfter']==35
class Fixture(BaseHTTPRequestHandler):
 calls=0
 def log_message(self,*args):pass
 def do_GET(self):
  Fixture.calls+=1
  body='主、备用 CDN 均不可用（清单 HTTP 403），30 秒内暂停向官方请求'.encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers()
  self.wfile.write(body[:10]);self.wfile.flush();time.sleep(.02);self.wfile.write(body[10:])
with ThreadingHTTPServer(('127.0.0.1',0),Fixture) as server:
 threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}/entry'
 webplay.cooldowns.clear()
 for _ in range(2):
  try:webplay.new_session(dict(url=url,options=[]))
  except webplay.UpstreamFailure as e:
   assert e.details['httpStatus']==403 and e.details['retryAfter']==30 and e.details['retryable']
   assert '格式' not in str(e)
  else:raise AssertionError('wrapped error accepted as media')
 assert Fixture.calls==1,'cooldown must prevent a second upstream request'
 webplay.cooldowns.clear()
 p=subprocess.run([os.getenv('NODE_BIN','node'),str(root/'probe.mjs'),url,'--pipeline'],env={**os.environ,'IPTV_PROBE_MANAGED':'1'},capture_output=True,timeout=15,check=True)
 result=classify_result(json.loads(p.stdout.decode().splitlines()[-1]));assert result['httpStatus']==403 and result['retryAfter']==30 and result['inconclusive'] and result['retryable'],result
 assert assess(result,{})=='未能验证'
 server.shutdown()
for seed in (False,True):
 calls,saved,modes,_=exercise(['http://fixture.invalid/live'],lambda *_:result.copy(),seed=seed)
 assert len(calls)==2 and calls[1][2]-calls[0][2]>=30,calls
 r=saved['http://fixture.invalid/live'];assert r['last_attempt']['error_category']=='upstream_cooldown'
 if seed:assert r['speed']==GOOD['speed'] and modes[0]['mode']=='off'
long={**result,'retryAfter':120}
calls,saved,modes,_=exercise(['http://fixture.invalid/live'],lambda *_:long.copy(),seed=True)
assert len(calls)==1 and modes[0]['mode']=='off'
assert saved['http://fixture.invalid/live']['speed']==GOOD['speed']
print('PASS: shared Python/JS cases, split error response, 30-second no-request cooldown, retained prior results/manual state and long-delay deferral.')

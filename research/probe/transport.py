"""Same dispatch, same segment URLs; compare production Node and Python transports."""
import sys,os,json,time,subprocess,re
from pathlib import Path
from urllib.request import Request
from urllib.parse import urljoin,urlsplit
root=Path(__file__).resolve().parents[2]
p=root/'work/probe-comparison'
sys.path.insert(0,str(root))
import webplay
items=json.loads((p/'samples.json').read_text(encoding='utf8'))['items']
results=[]
for trial in range(3):
 for item in items[:2]:
  session=None;row=dict(trial=trial,name=item['name'],at=time.time(),pairs=[])
  try:
   info=webplay.new_session(item);session=webplay.sessions[info['session']];text,base=session['initial']
   for _ in range(5):
    ls=text.splitlines();vs=[(int(re.search(r'BANDWIDTH=(\d+)',l)[1]),urljoin(base,ls[i+1])) for i,l in enumerate(ls[:-1]) if l.startswith('#EXT-X-STREAM-INF')]
    if not vs:break
    vs.sort(key=lambda v:v[0],reverse=True)
    with session['opener'].open(Request(vs[0][1],headers=session['manifest']),timeout=12) as r:text=r.read(1024*1024).decode('utf8');base=r.url
   segs=[urljoin(base,l) for l in text.splitlines() if l and not l.startswith('#')]
   for address in segs[-2:]:
    headers=session['media'];pair=dict(host=urlsplit(address).hostname)
    def python():
     try:
      with session['opener'].open(Request(address,headers=headers),timeout=12) as r:return dict(status=r.status,bytes=len(r.read(4096)))
     except Exception as e:return dict(status=getattr(e,'code',None),error=type(e).__name__)
    def node():
     script="import {mediaFetch} from './media-http.mjs';try{const r=await mediaFetch(process.env.TEST_URL,{headers:JSON.parse(process.env.TEST_HEADERS),signal:AbortSignal.timeout(12000)});const c=await r.body.getReader().read();console.log(JSON.stringify({status:r.status,bytes:c.value?.length||0}));await r.body.cancel().catch(()=>{});}catch(e){console.log(JSON.stringify({error:e.message}));}"
     env={**os.environ,'TEST_URL':address,'TEST_HEADERS':json.dumps(headers)}
     r=subprocess.run(['node','--input-type=module','-e',script],cwd=root,env=env,capture_output=True,encoding='utf8',timeout=16)
     return json.loads(r.stdout.splitlines()[-1])
    if trial%2:pair['node']=node();pair['python']=python()
    else:pair['python']=python();pair['node']=node()
    row['pairs'].append(pair)
  except Exception as e:row['error']=type(e).__name__+': '+str(e)
  finally:
   if session:webplay.close_session(session['id'])
  results.append(row);(p/'transport.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(row,ensure_ascii=True),flush=True)
  time.sleep(8)

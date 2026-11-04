"""Independent urllib playback session and full-sample multi-frame decode; no database writes."""
import os,sys,json,time,subprocess,re
from pathlib import Path
from urllib.request import Request
from urllib.parse import urljoin
root=Path(__file__).resolve().parents[2]
p=root/'work/probe-comparison'
sys.path.insert(0,str(root))
import webplay
results=[]
for item in json.loads((p/'samples.json').read_text(encoding='utf8'))['items']:
 start=time.monotonic();session=None
 try:
  info=webplay.new_session(item);session=webplay.sessions[info['session']]
  text,base=session['initial']
  for _ in range(5):
   lines=text.splitlines(); variants=[(int(re.search(r'BANDWIDTH=(\d+)',l)[1]),urljoin(base,lines[i+1])) for i,l in enumerate(lines[:-1]) if l.startswith('#EXT-X-STREAM-INF')]
   if not variants:break
   variants.sort(reverse=True)
   with session['opener'].open(Request(variants[0][1],headers=session['manifest']),timeout=12) as r:text=r.read(1024*1024).decode('utf8');base=r.url
  segs=[urljoin(base,l.strip()) for l in text.splitlines() if l.strip() and not l.startswith('#')]
  if '#EXT-X-KEY:' in text:raise ValueError('encrypted stream requires independent player')
  chunks=[]
  for address in segs[-2:]:
   with session['opener'].open(Request(address,headers=session['media']),timeout=12) as r:chunks.append(r.read(32*1024*1024))
  raw=subprocess.run([os.environ.get('FFMPEG_BIN','ffmpeg'),'-hide_banner','-nostdin','-loglevel','info','-protocol_whitelist','pipe','-threads','1','-i','pipe:0','-map','0:v:0','-an','-vf','showinfo','-frames:v','60','-f','null','-'],input=b''.join(chunks),capture_output=True,timeout=20)
  stamps=[float(n) for n in re.findall(rb'pts_time:([\d.-]+)',raw.stderr)]
  result=dict(decoded_frames=len(stamps),pts_advance=max(stamps)-min(stamps) if stamps else 0,exit=raw.returncode,bytes=sum(map(len,chunks)))
 except Exception as e:result=dict(error=str(e))
 finally:
  if session:webplay.close_session(session['id'])
 row=dict(name=item['name'],url=item['url'],elapsed=round(time.monotonic()-start,2),result=result);results.append(row)
 (p/'independent-highest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(row,ensure_ascii=True),flush=True)
 time.sleep(8)

import os,json,subprocess,time,sys
from pathlib import Path
root=Path(__file__).resolve().parents[2]
p=root/'work/probe-comparison'
data=json.loads((p/'samples.json').read_text(encoding='utf8'))
edition=sys.argv[1]
if edition not in ('baseline','baseline-pipeline','revised','revised-pipeline','cadence-pipeline'):
    raise SystemExit('Unknown comparison edition')
pipeline=edition.endswith('-pipeline')
src=p/'baseline' if edition.startswith('baseline') else root
sys.path.insert(0,str(src))
from service import needs_recheck
from catalog import assess
results=[]
for item in data['items']:
    start=time.monotonic()
    env={**os.environ,'FFMPEG_BIN':os.environ.get('FFMPEG_BIN','ffmpeg'),'IPTV_PROBE_MANAGED':'1','IPTV_PROBE_OPTIONS':json.dumps(item['options'])}
    try:
        r=subprocess.run(['node',str(src/'probe.mjs'),item['url'],'--quick'],env=env,capture_output=True,encoding='utf8',timeout=65)
        result=json.loads(r.stdout.strip().splitlines()[-1])
        first=result.copy()
        if pipeline and needs_recheck(result,assess(item['previous'],data['settings'])=='保留'):
            time.sleep(12 if result.get('httpStatus')==607 else 8)
            env['IPTV_PROBE_RECHECK']='1'
            r=subprocess.run(['node',str(src/'probe.mjs'),item['url'],'--continuous'],env=env,capture_output=True,encoding='utf8',timeout=120)
            result=json.loads(r.stdout.strip().splitlines()[-1]);result['first_attempt']=first
    except Exception as e:result={'error':str(e)}
    row=dict(name=item['name'],url=item['url'],elapsed=round(time.monotonic()-start,2),assessment=assess(result,data['settings']),result=result)
    results.append(row)
    (p/(edition+'.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(row,ensure_ascii=True),flush=True)
    time.sleep(8)

"""Real decoder plus controlled timestamp/error boundary regressions."""
import os, subprocess, tempfile, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
NODE='node'
FFMPEG=os.getenv('FFMPEG_BIN','ffmpeg')
def decode(data,continuous=True,env=None):
    code="import {decodeFrames} from './playback.mjs';let b=[];for await(const c of process.stdin)b.push(c);try{console.log(JSON.stringify(await decodeFrames(Buffer.concat(b),"+str(continuous).lower()+")))}catch(e){console.log(JSON.stringify({error:e.message,evidence:e.decodeEvidence}))}"
    result=subprocess.run([NODE,'--input-type=module','-e',code],input=data,capture_output=True,cwd=ROOT,env=env,timeout=10,check=True)
    return json.loads(result.stdout)
clip=subprocess.check_output([FFMPEG,'-v','error','-f','lavfi','-i','color=c=blue:s=320x180:r=12','-t','1','-c:v','libx264','-f','mpegts','pipe:1'])
result=decode(clip)
assert result['frames']>=3 and result['pts_advanced'] and result['resolution']['height']==180,result
quick=decode(clip,False)
assert quick['frames']>=1 and quick['resolution']['height']==180,quick
assert 'error' in decode(b'not media')
assert 'error' in decode(b'x',env={**os.environ,'FFMPEG_BIN':'/nonexistent/iptv-decoder'})
with tempfile.TemporaryDirectory() as folder:
    fake=Path(folder)/'decoder'
    fake.write_text('#!'+sys.executable+'\nimport sys,os,json\nsys.stdin.buffer.read()\nfor n,p in enumerate(json.loads(os.environ["FIXTURE_PTS"])):\n sys.stderr.write(f"[Parsed_showinfo_0] n: {n} pts: {n} pts_time:{p} pos: 0 fmt:yuv420p s:1280x720 i:P\\n")\nsys.exit(int(os.environ.get("FIXTURE_EXIT","0")))\n')
    fake.chmod(0o755)
    for pts in ([0,0,0],[2,1,3],[0,1],['N/A',1,2]):
        r=decode(b'x',env={**os.environ,'FFMPEG_BIN':str(fake),'FIXTURE_PTS':json.dumps(pts)})
        assert 'error' in r,(pts,r)
        assert r['evidence']['state']=='not_verified',r
    r=decode(b'x',env={**os.environ,'FFMPEG_BIN':str(fake),'FIXTURE_PTS':'[0,1,2]','FIXTURE_EXIT':'1'})
    assert 'error' in r,r
    r=decode(b'x',env={**os.environ,'FFMPEG_BIN':str(fake),'FIXTURE_PTS':'[0,0,0]'},continuous=False)
    assert r['frames']==3 and not r['pts_advanced'],r
print('PASS: real multiframe decode, quick fallback, corrupt data, insufficient/repeated/reversed/invalid PTS, decoder exit failure')

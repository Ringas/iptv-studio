"""Playback-probe regressions using locally generated, in-memory test media."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import tempfile
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import Counter
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parent

def verify_deferred_607():
    import service
    url='http://fixture.invalid/entry'
    failed={'method':'continuous','quality':'未能验证','httpStatus':607,'note':['HTTP 607']}
    good={'method':'continuous','quality':'流畅','opening':'直连可播','advanced':True,'speed':900,'seconds':24,'note':[]}
    with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
        service.init()
        with service.db() as c:
            c.execute('delete from sources')
            item={'name':'CCTV1','attrs':{},'options':[],'url':url}
            c.execute("insert into sources(name,kind,enabled,priority,items) values('测试','playlist',1,1,?)",(json.dumps([item]),))
        for last in (failed,good):
            with service.db() as c:c.execute('delete from tests')
            service.job.update(stop_queue=False)
            clock=iter(range(100,10000,31))
            with patch.object(service,'probe',side_effect=[failed.copy(),last.copy()]) as probe,patch.object(service.time,'monotonic',side_effect=lambda:next(clock)),patch.object(service,'gateway_delay',return_value=0):
                service.run('probe',[url])
            assert probe.call_count==2 and service.job['done']==service.job['total']==2,service.job
            with service.db() as c:line=service.catalog(c)[0]['lines'][0]
            assert line['assessment']==('保留' if last is good else '未能验证') and not line['test'].get('grace')
            assert line['test']['direct_history']==[0]
            if last is good: assert line['test']['opening']=='复测后可播'
        with patch.object(service,'probe',side_effect=lambda *_:good.copy()):
            for _ in range(5):service.run('probe',[url])
        with service.db() as c:assert service.catalog(c)[0]['lines'][0]['test']['direct_history']==[1]*5
        from catalog import quality_order
        def order(openings,speed,quality='流畅'):
            return quality_order({'assessment':'保留','url':url,'priority':1,'test':{**good,'direct_history':openings,'speed':speed,'quality':quality}},'speed')
        assert order([1]*5,1000)<order([1,0,0,1,0],9000)<order([1]*5,10000,'可播但不稳')

def verify_job_entry(media_base,entry_times):
    import service
    from app import Handler as JobHandler
    paths=['/entry-seven?fixture=7','/entry-eight?fixture=8']
    items=[dict(name=name,attrs={},options=['#EXTVLCOPT:http-referrer=https://fixture.invalid/'],url=media_base+path)
           for name,path in zip(('CCTV7','CCTV8'),paths)]
    with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
        service.init()
        with service.db() as c:
            c.execute('delete from sources')
            c.execute('delete from tests')
            c.execute("insert into sources(name,kind,enabled,priority,items) values('模拟订阅','playlist',1,1,?)",(json.dumps(items),))
        api=ThreadingHTTPServer(('127.0.0.1',0),JobHandler)
        threading.Thread(target=api.serve_forever,daemon=True).start()
        base=f'http://127.0.0.1:{api.server_port}'
        try:
            data=json.dumps(dict(action='probe',urls=[item['url'] for item in items])).encode()
            with urlopen(Request(base+'/api/job',data=data,headers={'Content-Type':'application/json'})) as r:assert json.load(r)['ok']
            deadline=time.monotonic()+150
            while service.job['running'] and time.monotonic()<deadline:time.sleep(.25)
            assert not service.job['running'] and service.job['done']==service.job['total']==3,service.job
            with urlopen(base+'/api/state') as r:channels=json.load(r)['channels']
            assert {ch['lines'][0]['url'] for ch in channels}=={item['url'] for item in items}
            for ch in channels:
                line=ch['lines'][0]
                result=line['test']
                assert line['assessment']=='保留' and result['advanced'] and result['resolution']['height']==180,result
                assert result.get('entry_retries',0)==int(line['url'].endswith('=7')),result
                recovered=line['url'].endswith('=7')
                assert result['opening']==('复测后可播' if recovered else '直连可播')
                assert result['direct_history']==[int(not recovered)]
                assert 0<result['play_seconds']<=result['seconds']
                if recovered:
                    assert result['startup']>0 and result['quality']=='流畅',result
                    first,second=entry_times[paths[0]]
                    assert second-first>=12,entry_times
                else:assert result['method']=='quick' and result['quality']=='初检可播',result
        finally:
            api.shutdown();api.server_close()

def main():
    verify_deferred_607()
    clip=subprocess.check_output([os.getenv('FFMPEG_BIN','ffmpeg'),'-v','error','-f','lavfi','-i',
        'color=c=blue:s=320x180:r=12','-t','1','-c:v','libx264','-g','12','-f','mpegts','pipe:1'])
    started=time.monotonic()
    entries=Counter()
    entry_times={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            path=self.path
            code=200
            if self.headers.get('Referer')!='https://fixture.invalid/':
                code=403;body=b'headers missing'
            elif path in ('/entry-seven?fixture=7','/entry-eight?fixture=8'):
                entry_times.setdefault(path,[]).append(time.monotonic())
                # A cached dispatch keeps returning the refusing node until the client revalidates.
                live=path.endswith('=8') or 'no-cache' in self.headers.get('Cache-Control','')
                if not live:time.sleep(6.1)
                self.send_response(302);self.send_header('Location','/live' if live else '/node607');self.end_headers()
                return
            elif path=='/cookie-entry':
                self.send_response(302);self.send_header('Location','/cookie/live')
                self.send_header('Set-Cookie','session=fixture; Path=/cookie; HttpOnly')
                self.end_headers();return
            elif path=='/warm-start':
                entries[path]+=1
                entry_times.setdefault(path,[]).append(time.monotonic())
                if entries[path]==1:
                    code=503;body=b'upstream warming up'
                else:
                    self.send_response(302);self.send_header('Location','/live');self.end_headers();return
            elif path.startswith('/cookie/') and self.headers.get('Cookie')!='session=fixture':
                code=403;body=b'cookie missing'
            elif path in ('/redispatch403','/refused403','/entry403'):
                entries[path]+=1
                entry_times.setdefault(path,[]).append(time.monotonic())
                if path=='/entry403':code=403;body=b'entry denied'
                else:
                    target='/live' if path=='/redispatch403' and entries[path]>1 else '/media403'
                    self.send_response(302);self.send_header('Location',target);self.end_headers();return
            elif path in ('/redispatch','/legacy-redispatch','/refused'):
                entries[path]+=1
                target='/live' if path!='/refused' and entries[path]>1 else '/node607'
                self.send_response(302);self.send_header('Location',target);self.end_headers()
                return
            elif path=='/master':
                body=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000\nhigh\n#EXT-X-STREAM-INF:BANDWIDTH=500000\nlive\n'
            elif path=='/cadence':
                entry_times.setdefault(path,[]).append(time.monotonic())
                seq=int((time.monotonic()-started)//6)
                rows=''.join(f'#EXTINF:1,\n/live/{i}.ts\n' for i in range(seq,seq+5))
                body=f'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:{seq}\n{rows}'.encode()
            elif path in ('/live','/fixed','/high','/slow','/corrupt','/node607','/cookie/live','/delayed','/media403'):
                seq=0 if path=='/fixed' else int(time.monotonic()-started)
                rows=''.join(f'#EXTINF:1,\n{path}/{i}.ts\n' for i in range(seq,seq+5))
                body=f'#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:{seq}\n{rows}'.encode()
            elif path.startswith('/high/'):
                code=404;body=b'missing'
            elif path.startswith('/node607/'):
                code=607;body=b''
            elif path.startswith('/media403/'):
                code=403;body=b'media denied'
            elif path.endswith('.ts'):
                if path.startswith('/slow/'):time.sleep(1.3)
                if path.startswith('/delayed/'):
                    entries['decode-sample']+=1
                    body=clip[:188*3] if entries['decode-sample']==1 else clip
                else:body=b'not a video' if path.startswith('/corrupt/') else clip
            else:
                body=b'{"error":"authentication required"}'
            self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers()
            self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def run(case):
        path,mode=case
        env={**os.environ,'IPTV_PROBE_OPTIONS':json.dumps(['#EXTVLCOPT:http-referrer=https://fixture.invalid/'])}
        raw=subprocess.check_output(['node',str(ROOT/'probe.mjs'),f'http://127.0.0.1:{server.server_port}/'+path,
                                     '--'+mode],env=env,timeout=80)
        return json.loads(raw.splitlines()[-1])
    cases=[('master','continuous'),('fixed','continuous'),('slow','continuous'),('live','quick'),('corrupt','quick'),('json','quick'),
           ('redispatch','quick'),('refused','quick'),('legacy-redispatch','legacy'),
           ('cookie-entry','quick'),('delayed','quick'),('warm-start','quick')]
    try:
        # Stay below the small local HTTP server's accept backlog.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            job_check=pool.submit(verify_job_entry,f'http://127.0.0.1:{server.server_port}',entry_times)
            good,stale,slow,quick,bad,unsupported,recovered,refused,legacy,cookie,delayed,warmed=pool.map(run,cases)
            job_check.result()
        recovered403=run(('redispatch403','quick'))
        refused403=run(('refused403','quick'))
        entry403=run(('entry403','quick'))
        cadence=run(('cadence','continuous'))
        intervals=[b-a for a,b in zip(entry_times['/cadence'],entry_times['/cadence'][1:])]
        assert cadence['advanced'] and cadence['resolution'] and len(intervals)>=2,cadence
        assert min(intervals)>=5.8,intervals
        print('Target-duration 6s playlist request intervals:',intervals)
        assert recovered403['advanced'] and recovered403['entry_retries']==1 and entries['/redispatch403']==2,recovered403
        assert entry_times['/redispatch403'][1]-entry_times['/redispatch403'][0]>=8
        assert refused403['quality']=='未能验证' and refused403['httpStatus']==403 and entries['/refused403']==2,refused403
        assert refused403.get('speed') is None and not refused403.get('resolution'),refused403
        assert entry403['httpStatus']==403 and entries['/entry403']==1 and not entry403.get('retryable'),entry403
        assert good['quality']=='流畅' and good['advanced'] and good['bitrate']==500000,good
        assert good['resolution']['height']==180 and good['startup']>0 and good['segments']>=3,good
        assert stale['quality']=='暂时失败' and '停更' in str(stale),stale
        assert slow['quality']=='可播但不稳' and slow['stalls']>0,slow
        assert quick['quality']=='初检可播' and quick['segments']>=2 and quick['advanced'],quick
        assert quick['seconds']<good['seconds'],(quick,good)
        assert bad['quality']==unsupported['quality']=='未能验证',(bad,unsupported)
        assert recovered['quality']=='初检可播' and recovered['advanced'] and recovered['resolution']['height']==180,recovered
        assert recovered['entry_retries']==1 and recovered['intermittent'] and entries['/redispatch']==2
        assert refused['quality']=='未能验证' and refused['httpStatus']==607 and entries['/refused']==2,refused
        assert refused.get('speed') is None and not refused.get('resolution')
        assert legacy['entry_retries']==1 and legacy['seconds']==8 and legacy['speed']==legacy['bytes']/8000
        assert cookie['advanced'] and cookie['resolution']['height']==180,cookie
        assert delayed['advanced'] and delayed['resolution']['height']==180 and '拼接' in str(delayed),delayed
        assert warmed['quality']=='初检可播' and warmed['entry_retries']==1 and entries['/warm-start']==2,warmed
        assert entry_times['/warm-start'][1]-entry_times['/warm-start'][0]>=6,entry_times
        print(f"Fixture timing: quick={quick['seconds']}s, continuous={good['seconds']}s; both decoded advancing media.")
        print('PASS: headers, first frame, variant fallback, advancing/frozen/stalling streams, HTTP 607 entry retries, legacy measurement')
    finally:
        server.shutdown();server.server_close()
if __name__=='__main__':main()

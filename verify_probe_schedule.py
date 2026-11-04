"""Offline pacing/retry regressions. No user streams or user database are opened."""
import json,tempfile
from pathlib import Path
from unittest.mock import patch
import service

GOOD={'quality':'初检可播','opening':'直连可播','advanced':True,'speed':800,'seconds':3,'note':[]}
TIMEOUT={'quality':'暂时失败','status':'暂时失败','retryable':True,'note':['入口超时']}

class Clock:
    def __init__(self):self.now=100.
    def read(self):return self.now
    def sleep(self,seconds):self.now+=seconds

def exercise(urls,callback,seed=False):
    with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
        service.init();clock=Clock();calls=[]
        service.gateway_windows.clear();service.gateway_limits.clear()
        with service.db() as db:
            items=[dict(name='CCTV'+str(i+1),url=u,attrs={},options=[]) for i,u in enumerate(urls)]
            db.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(json.dumps(items),))
            if seed:
                db.execute('insert into tests values(?,?)',(urls[0],service.encode(GOOD)))
                db.execute('insert into route_modes values(?,?,?)',(items[0]['name'],urls[0],'off'))
        def probe(url,method,*_):
            calls.append((url,method,clock.now))
            result=callback(url,method,calls)
            return {**result,'note':list(result.get('note',[]))}
        service.job.update(running=True,stop_queue=False,stop_line=False)
        with patch.object(service,'probe',side_effect=probe),patch.object(service.time,'monotonic',clock.read),patch.object(service.time,'sleep',clock.sleep),patch.object(service,'gateway_clock',clock.read):
            service.run('probe',urls)
        with service.db() as db:
            tests={r['url']:json.loads(r['result']) for r in db.execute('select * from tests')}
            modes=list(db.execute('select * from route_modes'))
        assert not service.job['message'].startswith('任务中断'),service.job
        return calls,tests,modes,clock.now

def main():
    a,b='https://gateway.test/a','https://other.test/b'
    calls,tests,_,_=exercise([a,b],lambda u,m,c:TIMEOUT if u==a and len(c)==1 else {**GOOD,'quality':'流畅' if m=='continuous' else '初检可播'})
    assert [u for u,_,_ in calls]==[a,b,a],calls
    assert [m for _,m,_ in calls]==['quick','quick','continuous'],calls
    assert calls[2][2]-calls[0][2]>=8,calls
    assert tests[a]['opening']=='复测后可播' and tests[a]['direct_history']==[0],tests
    assert tests[a]['recent']==[1] and any('入口超时' in s for s in tests[a]['note']),tests
    assert service.job['message']=='测速完成 · 2 条通过 · 0 条未通过',service.job

    calls,tests,_,_=exercise([a],lambda *_:{'quality':'未能验证','retryable':False,'httpStatus':403,'note':['鉴权失败']})
    assert len(calls)==1 and tests[a]['quality']=='未能验证'
    calls,_,_,_=exercise([a],lambda *_:{'quality':'未能验证','retryable':False,'note':['加密线路，不支持解码']})
    assert len(calls)==1

    def rate_then_good(u,m,calls):
        return {'httpStatus':429,'retryAfter':7,'note':['限流']} if len(calls)==1 else GOOD
    calls,tests,_,_=exercise([a,'https://gateway.test/c',b],rate_then_good)
    assert calls[1][0]==b,calls
    assert all(t-calls[0][2]>=7 for u,_,t in calls[1:] if 'gateway.test' in u),calls
    assert sum(u==a for u,_,_ in calls)==2 and tests[a]['entry_retries']==1,tests

    calls,tests,modes,_=exercise([a],lambda *_:{'httpStatus':429,'retryAfter':300,'note':['长时间冷却']},seed=True)
    assert len(calls)==1 and tests[a]==GOOD and len(modes)==1
    assert service.job['message']=='测速完成 · 0 条通过 · 0 条未通过 · 1 条待确认'

    calls,tests,_,clock=exercise([a],lambda *_:{'httpStatus':429,'retryAfter':6,'note':['持续限流']})
    assert len(calls)==2 and not tests,calls
    assert service.gateway_windows['gateway.test'][1]-clock>=12

    clock=Clock();service.gateway_windows.clear();service.gateway_limits.clear()
    with patch.object(service,'gateway_clock',clock.read):
        for i in range(3):
            url=f'https://gateway.test/{i}'
            clock.sleep(service.gateway_delay(url));assert service.gateway_delay(url,reserve=True)==0
        assert service.gateway_delay('https://gateway.test/next')>=6
        clock.sleep(6);assert service.gateway_delay('https://gateway.test/next',reserve=True)==0
        assert service.gateway_delay('https://gateway.test/next')>=2.5
    print('PASS: adaptive quick-first, transient retry after 8s, ready-host scheduling, auth/format no retry, per-entry pacing, exponential 429 cooldown and preserved measurements/modes.')

if __name__=='__main__':main()

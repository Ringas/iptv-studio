"""Bounded first-pass scheduling. Measurement policy lives in the existing probe."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import threading
import time
import uuid

context = threading.local()
active_states = {}

def host(url):
    return (urlsplit(url).hostname or '').lower().rstrip('.')

def lease_name(hostname):
    return hashlib.sha256(hostname.encode()).hexdigest()

def release_leases(directory, owner):
    for path in Path(directory).glob('*.lease'):
        try:
            if (path/'owner').read_text()==owner:
                (path/'owner').unlink()
                path.rmdir()
        except FileNotFoundError:
            pass

def store_cooldown(directory, hostname, seconds):
    path=Path(directory)/(lease_name(hostname)+'.cool')
    until=time.time()+seconds
    try:until=max(until,json.loads(path.read_text())['until'])
    except FileNotFoundError:pass
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps({'until':until}))
    temp.replace(path)

def first_pass(urls, routes, probe, job, lock, gateway_delay, cool_gateway, directory):
    """At most two sessions; same entry hostname never overlaps. No cold retries here."""
    pending=list(dict.fromkeys(urls));active={};results={}
    def worker(url,state):
        context.state=state
        try:
            with lock:stopped=state['stop'] or job['stop_queue']
            if stopped:return {},time.monotonic(),stopped
            result=probe(url,'pipeline',routes.get(url,{}).get('options',[]))
            return result,time.monotonic(),state['stop']
        except Exception as exc:
            return {'quality':'未能验证','retryable':True,'inconclusive':True,'note':[str(exc)]},time.monotonic(),state['stop']
        finally:
            release_leases(directory,state['owner'])
            del context.state
    with ThreadPoolExecutor(max_workers=2) as pool:
        while pending or active:
            with lock:stopping=job['stop_queue']
            if stopping:
                for url in pending:results[url]=({},time.monotonic(),'已终止')
                pending.clear()
            occupied={host(u) for u,_ in active.values()}
            for url in list(pending):
                if len(active)>=2 or stopping:break
                if host(url) in occupied:continue
                delay=gateway_delay(url)
                if delay>30:
                    results[url]=({'httpStatus':429,'deferred':True,'note':['入口冷却未结束，保留原结果']},time.monotonic(),False)
                    pending.remove(url);continue
                if delay>0:continue
                with lock:
                    if job['stop_queue']:break
                    delay=gateway_delay(url,reserve=True)
                    if delay:continue
                    state={'url':url,'owner':uuid.uuid4().hex,'directory':str(directory),'stop':False,'process':None,'sample':None,'sample_seconds':0,'rechecking':False,'channel':' / '.join(routes.get(url,{}).get('channels',[]))}
                    active_states[url]=state
                    job.setdefault('active_lines',{})[url]={'url':url,'sample':None,'sample_seconds':0}
                    job.update(active_url=next(iter(job['active_lines'])),active_channel='双线路检测',message='双线路初测 · '+str(len(results))+'/'+str(len(urls)))
                    future=pool.submit(worker,url,state)
                    active[future]=(url,state)
                pending.remove(url);occupied.add(host(url))
            if not active:
                if pending:time.sleep(.05)
                continue
            done,_=wait(active,timeout=.05,return_when=FIRST_COMPLETED)
            for future in done:
                url,state=active.pop(future);result,finished,stopped=future.result()
                if result.get('httpStatus')==429 and not result.get('deferred'):
                    if gateway_delay(url)<=0:cool_gateway(url,result.get('retryAfter'))
                    result['_entry_cooled']=True
                results[url]=(result,finished,stopped)
                with lock:
                    active_states.pop(url,None)
                    job['active_lines'].pop(url,None)
                    job.update(active_url=next(iter(job['active_lines']),None),done=len(results))
    return results

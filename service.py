import gzip
import hashlib
import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import math
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from time import monotonic as gateway_clock
from email.utils import parsedate_to_datetime
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError
from http.client import HTTPException
from urllib.parse import urlsplit,urljoin
from grouping import programme_profiles
from eit import programmes
from catalog import parse_playlist, playlist_guides, build, selected, guide, xmlroot, xmltv_logos, normalize, assess, speed_display

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv('DATA_DIR',ROOT/'data'))
LOCK=threading.RLock()
job={'running':False,'message':'空闲','done':0,'total':0,'active_url':None,'sample':None,'revision':0}
probe_process=None
DEFAULT={'minutes':360,'auto_probe':False,'strict':True,'mode':'merge','next':0,
         'probe_mode':'adaptive','criteria':'speed','min_speed':100,'min_height':720}

# The same gateway can serve several subscriptions. Share its quiet period across jobs.
gateway_windows={}
gateway_limits={}
gateway_lock=threading.Lock()

def gateway_delay(url,reserve=False):
    entry=urlsplit(url)
    if entry.scheme not in ('http','https'):return 0
    host=entry.netloc.lower()
    now=gateway_clock()
    with gateway_lock:
        last,blocked,opened=gateway_windows.get(host,(0,0,set()))
        if now-last>=6:opened=set()
        # Pace every entry opening, including retries of the same channel.
        until=max(blocked,last+2.5 if last else 0,last+6 if len(opened)>=3 and url not in opened else 0)
        if until>now:return until-now
        if reserve:
            opened.add(url)
            gateway_windows[host]=(now,0,opened)
        return 0

def cool_gateway(url,seconds):
    host=urlsplit(url).netloc.lower()
    with gateway_lock:
        now=gateway_clock()
        hits,at=gateway_limits.get(host,(0,0))
        hits=hits+1 if now-at<120 else 1
        gateway_limits[host]=(hits,now)
        last,blocked,opened=gateway_windows.get(host,(0,0,set()))
        gateway_windows[host]=(last,max(blocked,now+max(min(120,6*2**min(hits-1,5)),seconds or 0)),opened)

def needs_recheck(result,was_usable=False):
    if result.get('quality')=='可播但不稳':return True
    if result.get('quality') not in ('暂时失败','未能验证') and result.get('status')!='测速失败':return False
    return was_usable or result.get('retryable',False) or result.get('httpStatus')==607 or any('快检观察期' in note for note in result.get('note',[]))

@contextmanager
def db():
    conn=sqlite3.connect(DATA/'hub.sqlite',timeout=20)
    conn.row_factory=sqlite3.Row
    try:
        with conn: yield conn
    finally: conn.close()

def encode(x): return json.dumps(x,ensure_ascii=False)
def config(c): return {**DEFAULT,**json.loads(c.execute("select value from settings").fetchone()[0])}
def save_config(c, cfg): c.execute("update settings set value=?",(encode(cfg),))
def log(c,text,kind='history',**fields):
    c.execute("insert into logs(ts,message,kind,details) values(?,?,?,?)",
              (int(time.time()),str(text)[:1200],kind,encode(fields)))
    c.execute("delete from logs where kind=? and id < (select id from logs where kind=? order by id desc limit 1 offset 249)",(kind,kind))

def hours_value(value):
    hours=float(value)
    if not math.isfinite(hours) or not (hours==0 or 1/60<=hours<=720):
        raise ValueError('更新间隔应为 0（关闭），或 1 分钟至 720 小时')
    return hours

def deadline(hours,enabled=True):
    return int(time.time())+round(hours*3600) if enabled and hours else 0

def init():
    DATA.mkdir(parents=True,exist_ok=True)
    with db() as c:
        c.executescript("""
        pragma journal_mode=WAL;
        create table if not exists settings(value text);
        create table if not exists admin_access(id integer primary key check(id=1), salt blob not null, verifier blob not null);
        create table if not exists sources(id integer primary key,name text,kind text,url text,priority integer,enabled integer,items text default '[]',xml blob,last text default '',updated integer default 0);
        create table if not exists tests(url text primary key,result text);
        create table if not exists channel_logos(name text primary key,digest text not null,png blob not null);
        create table if not exists rules(name text primary key,value text);
        create table if not exists route_modes(name text,url text,mode text not null,primary key(name,url));
        create table if not exists hidden_items(kind text,name text,url text,primary key(kind,name,url));
        create table if not exists restored_channel_routes(channel text,url text,primary key(channel,url));
        create table if not exists restored_routes(group_name text,channel text,url text,primary key(group_name,channel,url));
        create table if not exists logs(id integer primary key,ts integer,message text);
        create table if not exists playlist_epg(source_id integer, url text, primary key(source_id,url));
        create table if not exists eit_events(source_id integer,name text,url text,start integer,stop integer,title text,description text,primary key(source_id,name,url,start));
        create table if not exists epg_cache(url text primary key, xml blob, profiles text, logos text, programmes integer);
        create view if not exists active_epg as
            select url,priority,embedded,owner,position from (
              select *,row_number() over(partition by url order by priority,embedded,owner,position) as rank from (
                select url,priority,0 as embedded,id as owner,0 as position from sources where enabled and kind='epg'
                union all select e.url,s.priority,1,s.id,e.rowid from playlist_epg e join sources s on s.id=e.source_id
                where s.enabled and s.kind='playlist'
              )
            ) where rank=1;
        """)
        if 'kind' not in {r['name'] for r in c.execute('pragma table_info(logs)')}:
            c.execute("alter table logs add column kind text not null default 'history'")
            c.execute("alter table logs add column details text not null default '{}'")
        if 'interval_hours' not in {r['name'] for r in c.execute('pragma table_info(sources)')}:
            c.execute('begin immediate')
            c.execute('alter table sources add column interval_hours real')
            c.execute('alter table sources add column next_update integer not null default 0')
            if c.execute('select 1 from settings').fetchone():
                cfg=config(c)
                if cfg['minutes']:
                    c.execute('update sources set next_update=? where enabled',(cfg['next'] or int(time.time()),))
        if 'profiles' not in {r['name'] for r in c.execute('pragma table_info(sources)')}:
            c.execute('alter table sources add column profiles text')
        for src in list(c.execute("select id,xml from sources where kind='epg' and xml is not null and profiles is null")):
            c.execute('update sources set profiles=? where id=?',(encode(programme_profiles(xmlroot(src['xml']))),src['id']))
        if 'logos' not in {r['name'] for r in c.execute('pragma table_info(sources)')}:
            c.execute('alter table sources add column logos text')
        for src in list(c.execute("select id,url,xml from sources where kind='epg' and xml is not null and logos is null")):
            c.execute('update sources set logos=? where id=?',(encode(xmltv_logos(xmlroot(src['xml']),src['url'])),src['id']))
        for src in c.execute("select * from sources where kind='epg' and xml is not null order by priority,id").fetchall():
            count=len(xmlroot(src['xml']).findall('programme'))
            c.execute('insert or ignore into epg_cache values(?,?,?,?,?)',(src['url'],src['xml'],src['profiles'],src['logos'],count))
            c.execute('update sources set xml=null,profiles=null,logos=null where id=?',(src['id'],))
        if c.execute('select count(*) from settings').fetchone()[0]:
            # Old decorated keys can still own a manual override after names are cleaned.
            for old,value in list(c.execute('select * from rules')):
                key=normalize(old)
                rule=json.loads(value)
                target=normalize(rule.get('target',''))
                if key==old and target==rule.get('target',''): continue
                rule['target']=target if target!=key else ''
                c.execute('delete from rules where name=?',(old,))
                c.execute('insert or ignore into rules values(?,?)',(key,encode(rule)))
            for old,url,mode in list(c.execute('select * from route_modes')):
                key=normalize(old)
                if key==old: continue
                c.execute('delete from route_modes where name=? and url=?',(old,url))
                c.execute('insert or ignore into route_modes values(?,?,?)',(key,url,mode))
            return
        c.execute('insert into settings values(?)',(encode({**DEFAULT,'next':int(time.time())+21600}),))


def catalog(c,hidden_groups=None,hidden_channels=None,hidden_items=None):
    sources=[{**dict(s),'items':json.loads(s['items'])} for s in c.execute("select id,name,url,priority,items from sources where enabled=1 and kind='playlist' order by priority,id")]
    tests={r[0]:json.loads(r[1]) for r in c.execute('select * from tests')}
    rules={r[0]:json.loads(r[1]) for r in c.execute('select * from rules')}
    modes={(r['name'],r['url']):r['mode'] for r in c.execute('select * from route_modes')}
    profiles={}
    logos={}
    for row in c.execute("select profiles,logos from epg_cache join active_epg using(url) order by priority,embedded,owner,position"):
        for key,profile in json.loads(row['profiles'] or '{}').items():profiles.setdefault(key,profile)
        for key,logo in json.loads(row['logos'] or '{}').items():logos.setdefault(key,logo)
    hidden={tuple(r) for r in c.execute('select kind,name,url from hidden_items')}
    restored={tuple(r) for r in c.execute('select group_name,channel,url from restored_routes')}
    channel_routes={tuple(r) for r in c.execute('select channel,url from restored_channel_routes')}
    return build(sources,tests,rules,modes,config(c),profiles,hidden,logos,restored,hidden_groups,channel_routes,hidden_channels,hidden_items)

def epg(c, channels):
    events=c.execute("""select e.* from eit_events e join sources s on e.source_id=s.id
                        where s.enabled and s.kind='playlist' and e.stop>? order by s.priority,s.id,e.start,e.url""",(int(time.time()),))
    return guide(channels,(r[0] for r in c.execute("select xml from epg_cache join active_epg using(url) order by priority,embedded,owner,position")),events)

def weburl(url):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
        raise ValueError('订阅地址必须是 HTTP(S) URL，且不能包含用户名或密码')
    return url

class Redirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        weburl(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch(url,limit):
    req=Request(weburl(url),headers={'User-Agent':'IPTV-Hub/1.0','Accept-Encoding':'gzip'})
    with build_opener(Redirect()).open(req,timeout=20) as response:
        raw=response.read(limit+1)
        base=response.geturl()
    if len(raw)>limit: raise ValueError('订阅超过大小限制')
    if raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream: raw=stream.read(limit+1)
        if len(raw)>limit: raise ValueError('解压后超过限制')
    return raw,base

def update_guide(url,attempts):
    if url in attempts:
        count,error=attempts[url]
        if error:raise ValueError(error)
        return count
    try:
        raw,base=fetch(url,32*1024*1024)
        root=xmlroot(raw)
        count=len(root.findall('programme'))
        if not count: raise ValueError('XMLTV 没有节目，保留旧快照')
        with db() as c:
            c.execute('insert or replace into epg_cache values(?,?,?,?,?)',
                      (url,raw,encode(programme_profiles(root)),encode(xmltv_logos(root,base)),count))
    except Exception as exc:
        attempts[url]=(0,str(exc))
        raise
    attempts[url]=(count,None)
    return count

def prune_guides(c):
    c.execute("delete from epg_cache where url not in (select url from playlist_epg union select url from sources where kind='epg')")

def read_eit_routes(items):
    for item in items:
        url=item['url']
        if urlsplit(url).scheme not in ('http','https','udp','rtp'):continue
        while (delay:=gateway_delay(url,reserve=True)) and delay<=30:
            time.sleep(delay)
        if delay:continue
        try:
            env={**os.environ,'IPTV_PROBE_OPTIONS':encode(item.get('options',[]))}
            raw=subprocess.check_output(['node',str(ROOT/'probe.mjs'),url,'--eit'],env=env,timeout=12,stderr=subprocess.DEVNULL)
            sample=json.loads(raw)
            if sample.get('httpStatus')==429:cool_gateway(url,sample.get('retryAfter'))
            packets=base64.b64decode(sample.get('packets',''))
            rows=[e for e in programmes(packets,item) if e[1]>time.time()]
            if rows:return url,rows
        except (ValueError,OSError,subprocess.SubprocessError):
            continue
    return '',[]

def collect_eit(source,items):
    groups=defaultdict(list)
    for item in items:groups[normalize(item['name'])].append(item)
    found=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks={pool.submit(read_eit_routes,routes):name for name,routes in groups.items()}
        for i,task in enumerate(as_completed(tasks),1):
            name=tasks[task]
            url,events=task.result()
            if events:found.append((name,url,events))
            with LOCK:job['message']=f"读取 DVB 节目表 · {source['name']} · {i}/{len(tasks)} 个频道"
    return found

class EntryOnly(HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None

def manifest_hosts(url):
    # Inspect this gateway's response without following redirects or opening media segments.
    try:
        with build_opener(EntryOnly()).open(Request(url,headers={'User-Agent':'IPTV-Hub/1.0'}),timeout=3) as response:
            if response.read(7)!=b'#EXTM3U':return []
            lines=response.read(32768).decode('utf-8','replace').splitlines()
            hosts=set()
            expect_uri=False
            for line in lines:
                line=line.strip()
                if line.startswith(('#EXTINF:','#EXT-X-STREAM-INF:')):expect_uri=True
                elif expect_uri and line and not line.startswith('#'):
                    expect_uri=False
                    host=urlsplit(urljoin(url,line)).hostname
                    if host:hosts.add(host)
            return sorted(hosts)[:32]
    except HTTPError as error:
        with error:
            if 300<=error.code<400 and error.headers.get('Location'):
                host=urlsplit(urljoin(url,error.headers['Location'])).hostname
                return [host] if host else []
            if error.code==429:raise
    return []

def identify_gateway_routes(items,source):
    server=urlsplit(source['url'])
    cached={it['url']:it for it in json.loads(source['items'] or '[]')}
    pending=defaultdict(list)
    now=int(time.time())
    for it in items:
        url=it['url']
        endpoint=urlsplit(url)
        if endpoint.scheme not in ('https','http') or endpoint.netloc!=server.netloc:continue
        if not (endpoint.path.startswith(('/relay/','/proxy/')) or endpoint.path.strip('/').isdigit()):continue
        old=cached.get(url,{})
        if old.get('origin_checked',0)>now-86400:
            it.update(origin_hosts=old.get('origin_hosts',[]),origin_checked=old['origin_checked'])
        elif not it.get('options'):pending[url].append(it)
    deadline=time.monotonic()+15
    limited=threading.Event()
    def inspect(url):
        while not limited.is_set() and time.monotonic()<deadline:
            delay=gateway_delay(url,reserve=True)
            if not delay:break
            if time.monotonic()+delay>=deadline:return
            limited.wait(delay)
        else:return
        try: hosts=manifest_hosts(url)
        except HTTPError as exc:
            if exc.code==429:
                limited.set()
                header=exc.headers.get('Retry-After','')
                try:seconds=int(header) if header.isdigit() else parsedate_to_datetime(header).timestamp()-time.time()
                except (TypeError,ValueError,OverflowError):seconds=6
                cool_gateway(url,seconds)
            return
        except (OSError,ValueError,HTTPException):return
        for item in pending[url]:item.update(origin_hosts=hosts,origin_checked=now)
    if pending:
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(inspect,pending))

def refresh(source,attempts):
    linked_errors=[]
    items=[]
    if source['kind']=='playlist':
        raw,base=fetch(source['url'],32*1024*1024)
        text=raw.decode('utf-8-sig')
        items=parse_playlist(text,base)
        identify_gateway_routes(items,source)
        links=playlist_guides(text,base)
        for i,url in enumerate(links,1):
            try:update_guide(url,attempts)
            except Exception as exc:linked_errors.append(f'节目单 {i}：{exc}')
        message=f'成功 · {len(items)} 条'
        if links:message+=f' · 自带节目单 {len(links)-len(linked_errors)}/{len(links)} 份更新成功'
        found=[] if links else collect_eit(source,items)
        if not links:message+=f' · DVB EIT 本次读取 {len(found)} 个频道，{sum(len(e) for _,_,e in found)} 条节目'
    else:
        message=f"成功 · {update_guide(source['url'],attempts)} 条"
    with db() as c:
        if source['kind']=='playlist':
            sid=source['id']
            c.execute('delete from eit_events where stop<=?',(int(time.time()),))
            keep=set() if links else {(normalize(it['name']),it['url']) for it in items}
            old=c.execute('select distinct name,url from eit_events where source_id=?',(sid,)).fetchall()
            c.executemany('delete from eit_events where source_id=? and name=? and url=?',[(sid,n,u) for n,u in old if (n,u) not in keep])
            for name,url,events in found:
                for start,stop,title,desc in events:
                    c.execute('delete from eit_events where source_id=? and name=? and start<? and stop>?',(sid,name,stop,start))
                    c.execute('insert into eit_events values(?,?,?,?,?,?,?)',(sid,name,url,start,stop,title,desc))
            c.execute('delete from playlist_epg where source_id=?',(source['id'],))
            c.executemany('insert into playlist_epg values(?,?)',[(source['id'],u) for u in links])
            c.execute('update sources set items=? where id=?',(encode(items),source['id']))
        c.execute('update sources set last=?,updated=? where id=?',(message,int(time.time()),source['id']))
    return {item['url'] for item in items},linked_errors

def stop_probe(run_id,url=None):
    with LOCK:
        if not job['running'] or job.get('run_id')!=run_id or not job.get('probing'):
            raise ValueError('本次测速已结束')
        if url is not None and url!=job['active_url']:
            raise ValueError('这条线路的测速已结束')
        if url is None: job['stop_queue']=True
        if not job['stop_line']:
            job['stop_line']='已终止' if url is None else '已跳过'
        if probe_process is not None: probe_process.kill()

def probe(url,method,options):
    global probe_process
    if urlsplit(url).scheme not in ('http','https'):
        return {'method':method,'quality':'未能验证','status':'未能验证','note':['当前检测器仅支持 HTTP(S) HLS']}
    try:
        args=['node',str(ROOT/'probe.mjs'),url,'--progress']
        if method!='legacy':args.append('--'+method)
        with subprocess.Popen(args,env={**os.environ,'IPTV_PROBE_OPTIONS':encode(options),'IPTV_PROBE_MANAGED':'1' if method!='legacy' else '0',
                                      'IPTV_PROBE_RECHECK':'1' if job.get('rechecking') else '0'},stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,encoding='utf-8') as proc:
            with LOCK:
                probe_process=proc
                if job['stop_line']: proc.kill()
            timeout=threading.Timer(120 if method=='continuous' else 60,proc.kill)
            timeout.start()
            result=None
            try:
                for line in proc.stdout:
                    event=json.loads(line)
                    if event.get('sample'):
                        with LOCK: job.update(sample=event.get('phase') or speed_display(event.get('speed')),sample_seconds=event.get('seconds',0))
                    else: result=event
                if proc.wait()==0 and result is not None:return result
            finally:
                timeout.cancel()
                with LOCK: probe_process=None
            return {'status':'测速失败','quality':'暂时失败','retryable':True,'note':['测速进程未返回完整结果或已超时']}
    except FileNotFoundError: raise RuntimeError('测速需要安装 Node.js 22 或以上')

def progress(message,done,total):
    with LOCK: job.update(message=message,done=done,total=total)

def run(action,requested):
    failures=0
    try:
        if action in ('refresh','scheduled'):
            query='select * from sources where enabled'
            args=()
            updated_urls=set()
            guide_attempts={}
            if action=='scheduled':
                query+=' and next_update between 1 and ?'
                args=(int(time.time()),)
            with db() as c: sources=list(c.execute(query+' order by priority,id',args))
            for i,s in enumerate(sources):
                progress('更新 '+s['name'],i,len(sources))
                try:
                    urls,errors=refresh(s,guide_attempts)
                    updated_urls.update(urls)
                    failures+=bool(errors)
                    with db() as c:
                        row=c.execute('select last from sources where id=?',(s['id'],)).fetchone()
                        log(c,row['last'] if row else '更新完成','refresh',source=s['name'],result='部分失败' if errors else '成功',reason='；'.join(errors))
                except Exception as exc:
                    failures+=1
                    with db() as c:
                        msg='失败，保留旧快照 · '+str(exc)[:250]
                        c.execute('update sources set last=? where id=?',(msg,s['id']))
                        log(c,'保留旧快照','refresh',source=s['name'],result='失败',reason=str(exc)[:1600])
                if action=='scheduled':
                    with LOCK,db() as c:
                        hours=s['interval_hours'] if s['interval_hours'] is not None else config(c)['minutes']/60
                        c.execute('update sources set next_update=? where id=? and next_update=?',(deadline(hours),s['id'],s['next_update']))
            with db() as c:prune_guides(c)
        with db() as c: cfg=config(c)
        if action=='probe' or (action=='scheduled' and cfg['auto_probe'] and updated_urls):
            with db() as c: channels=catalog(c)
            routes={}
            for ch in channels:
                if requested is None and not ch['enabled']: continue
                for line in ch['lines']:
                    url=line['url']
                    if action=='scheduled' and url not in updated_urls: continue
                    info=routes.setdefault(url,{'channels':[], 'sources':[],'options':line.get('options',[]),'deep':False})
                    previous=line['test']
                    if previous.get('quality')=='可播但不稳' or previous.get('stalls',0)>=2:
                        info['deep']=True
                    if ch['label'] not in info['channels']: info['channels'].append(ch['label'])
                    for name in line['from']:
                        if name not in info['sources']: info['sources'].append(name)
            targets=list(requested) if requested is not None else list(routes)
            retries={}
            recheck_reasons={}
            passed=failed=uncertain=stopped=skipped=0
            outcomes={}
            with LOCK: job.update(probing=True,total=len(targets),done=0)
            limited_once={}
            i=0
            while i<len(targets):
                u=targets[i]
                def wait_for(address):return max(gateway_delay(address),retries[address]-time.monotonic() if address in retries else 0,0)
                if wait_for(u)>0:
                    ready=next((k for k in range(i+1,len(targets)) if wait_for(targets[k])<=0),None)
                    if ready is not None:
                        targets[i],targets[ready]=targets[ready],u
                        u=targets[i]
                info=routes.get(u,{'channels':[],'sources':[]})
                channel=' / '.join(info['channels'])
                with LOCK:
                    if job['stop_queue']: break
                    job.update(message=f'测速 {i+1}/{len(targets)} · {channel}',done=i,active_url=u,active_channel=channel,sample=None,sample_seconds=0,stop_line=False,rechecking=u in retries)
                method=cfg['probe_mode']
                if method=='adaptive':
                    method='continuous' if info.get('deep') or u in retries else 'quick'
                try:
                    delay=wait_for(u)
                    while 0<delay<=30 and not job['stop_line']:
                        with LOCK:job['sample']=f'{"间隔复测" if u in retries else "入口冷却"}，约 {math.ceil(delay)} 秒后测试'
                        time.sleep(min(delay,.25))
                        delay=wait_for(u)
                    if not delay:delay=gateway_delay(u,reserve=True)
                    if job['stop_line']:result={}
                    elif delay:result={'httpStatus':429,'deferred':True,'note':[f'入口仍在限流冷却，约 {math.ceil(delay)} 秒后可复测']}
                    else:result=probe(u,method,info.get('options',[]))
                except Exception as exc: result={'status':'测速失败','quality':'暂时失败','retryable':True,'note':[str(exc)]}
                with LOCK, db() as c:
                    fields=dict(channel=channel,source=' / '.join(info['sources']),url=u)
                    if job['stop_line']:
                        if job['stop_line']=='已跳过': skipped+=1
                        else: stopped+=1
                        outcomes[u]='skipped' if job['stop_line']=='已跳过' else 'stopped'
                        log(c,'已保留上次测速结果','probe',**fields,result=job['stop_line'])
                    elif result.get('httpStatus')==429:
                        reason='；'.join(result.get('note',[]))
                        if not result.get('deferred'):
                            cool_gateway(u,result.get('retryAfter'))
                            if u not in retries and gateway_delay(u)<=30:
                                limited_once[u]=reason
                                retries[u]=time.monotonic()+gateway_delay(u)
                                targets.append(targets.pop(i))
                                continue
                        uncertain+=1
                        outcomes[u]='uncertain'
                        log(c,'上游限流，保留原测速及启停状态','probe',**fields,result='待复测',reason=reason)
                    else:
                        if u in limited_once:
                            result.setdefault('note',[]).insert(0,'入口限流冷却后复测；此前响应：'+limited_once[u])
                        result.setdefault('at',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
                        prior=c.execute('select result from tests where url=?',(u,)).fetchone()
                        previous=json.loads(prior[0]) if prior else {}
                        direct_history=previous.get('direct_history',[])
                        if method!='legacy':
                            result.setdefault('method',method)
                            result.setdefault('quality','暂时失败')
                            # A deferred recheck belongs to the same independent test.
                            if u in retries:
                                if result.get('advanced'):result['opening']='复测后可播'
                                result['entry_retries']=result.get('entry_retries',0)+1
                                result.setdefault('note',[]).insert(0,'首次检测后已等待冷却，再从原入口复测；起播耗时为复测本次')
                                if u in recheck_reasons:result['note'].insert(1,'首次检测：'+recheck_reasons[u])
                                if u in limited_once:direct_history=(direct_history+[0])[-5:]
                            else:
                                direct_history=(direct_history+[int(result.get('opening')=='直连可播')])[-5:]
                            if result['quality'] in ('暂时失败','未能验证'):
                                was_usable=assess(previous,cfg)=='保留'
                                if was_usable or (previous.get('grace') and result['quality']=='未能验证'):
                                    result['grace']=True
                            if u not in retries and needs_recheck(result,assess(previous,cfg)=='保留'):
                                recheck_reasons[u]='；'.join(result.get('note',[]))[:500]
                                retries[u]=time.monotonic()+(12 if result.get('httpStatus')==607 else 8)
                                targets.append(u)
                                job['total']=len(targets)
                                result.setdefault('note',[]).append('已安排间隔复测，本轮最多复测一次')
                            history=previous.get('recent',[])
                            if u in retries and u not in limited_once:history=history[:-1]
                            result['recent']=(history+[int(result['quality'] in ('流畅','初检可播'))])[-5:]
                        if direct_history:result['direct_history']=direct_history
                        assessment=assess(result,cfg)
                        reason=[assessment,*result.get('note',[])]
                        if assessment=='速率未达标': reason.append(f"要求严格高于 {cfg['min_speed']} KB/s")
                        if assessment in ('分辨率未达标','分辨率未知'): reason.append(f"要求高度至少 {cfg['min_height']} 像素")
                        quality=result.get('quality')
                        if quality=='可播但不稳' and result.get('stalls',0)>0:quality='易卡顿'
                        metrics=[v for v in (result.get('opening'),quality) if v]
                        if result.get('httpStatus')==607:metrics.append('节点返回 607')
                        if result.get('entry_retries'):metrics.append(f"入口重连 {result['entry_retries']} 次")
                        if result.get('startup') is not None:metrics.append(f"本轮总起播耗时 {result['startup']} 秒")
                        if result.get('method')=='continuous' and result.get('stalls') is not None:
                            metrics.append(f"播放观察 {result.get('play_seconds',result['seconds'])} 秒 · 预计卡顿 {result['stalls']} 次")
                        if result.get('speed') is not None: metrics.append(speed_display(result['speed']))
                        size=result.get('resolution') or {}
                        if size.get('height'): metrics.append(f"{size.get('width','?')} × {size['height']}")
                        ok=assessment=='保留'
                        passed+=int(ok)
                        pending=assessment in ('未能验证','待复测')
                        failed+=int(not ok and not pending)
                        uncertain+=int(pending)
                        outcomes[u]='passed' if ok else 'uncertain' if pending else 'failed'
                        log(c,' · '.join(metrics),'probe',**fields,result='通过' if ok else assessment if assessment in ('未能验证','暂时失败','待复测') else '未通过',reason='' if ok else '；'.join(reason))
                        c.execute('insert or replace into tests values(?,?)',(u,encode(result)))
                        # The measurement is shared by all channel entries using this URL.
                        c.execute('delete from route_modes where url=?',(u,))
                    job.update(done=i+1,active_url=None,sample=None,revision=job['revision']+1)
                i+=1
            summary='测速已终止' if job['stop_queue'] else '测速完成'
            # Retries replace the earlier outcome for the same stream URL.
            passed,failed,uncertain,stopped,skipped=(sum(v==kind for v in outcomes.values()) for kind in ('passed','failed','uncertain','stopped','skipped'))
            message=f'{summary} · {passed} 条通过 · {failed} 条未通过'
            if uncertain: message+=f' · {uncertain} 条待确认'
            if skipped: message+=f' · {skipped} 条已跳过'
            if stopped: message+=f' · {stopped} 条已终止'
            remaining=len(set(targets)-outcomes.keys())
            if remaining: message+=f' · {remaining} 条未执行'
            if action=='scheduled' and failures: message+=f' · {failures} 个订阅更新失败'
        else:
            message=f'更新完成 · {len(sources)} 个订阅 · {failures} 个失败'
    except Exception as exc:
        message='任务中断 · '+str(exc)
    finally:
        with LOCK:
            if message.startswith('任务中断'):
                with db() as c: log(c,message)
            if action in ('scheduled','refresh'):job['revision']+=1
            job.update(running=False,message=message,probing=False,active_url=None,active_channel='',sample=None)

def start(action,url=None,urls=None):
    with LOCK:
        if job['running']: raise ValueError('已有任务运行中')
        if action not in ('probe','refresh','scheduled'): raise ValueError('未知任务')
        if url is not None:
            if urls is not None: raise ValueError('请只提交一组测试目标')
            urls=[url]
        if urls is not None:
            if action!='probe' or not isinstance(urls,list) or not urls or not all(isinstance(u,str) and u for u in urls):
                raise ValueError('请选择要测试的线路')
            with db() as c:
                available={r['url'] for ch in catalog(c) for r in ch['lines']}
            if not set(urls)<=available: raise ValueError('所选线路已变动，请刷新后重选')
            urls=list(dict.fromkeys(urls))
        job.update(run_id=uuid.uuid4().hex,stop_line=False,stop_queue=False,probing=False,running=True,action=action,message='准备任务',done=0,total=0)
        threading.Thread(target=run,args=(action,urls),daemon=True).start()

def scheduler():
    while True:
        time.sleep(5)
        with LOCK,db() as c:
            if not job['running'] and c.execute('select 1 from sources where enabled and next_update between 1 and ? limit 1',(int(time.time()),)).fetchone():
                start('scheduled')

def restore_hidden_routes(c,scope,owner,targets):
    if scope=='route':
        if owner is not None:raise ValueError('单独隐藏的线路无需指定父级')
    elif scope not in ('channel','group') or not isinstance(owner,str) or not owner:
        raise ValueError('请选择恢复范围')
    if not isinstance(targets,list) or not targets:raise ValueError('请选择要恢复的线路')
    keys=set()
    for target in targets:
        if not isinstance(target,dict) or not all(isinstance(target.get(k),str) and target[k] for k in ('name','url')):
            raise ValueError('无效的线路')
        keys.add((target['name'],target['url']))
    if scope=='route':
        c.executemany("delete from hidden_items where kind='route' and name=? and url=?",keys)
        return
    inventory={}
    if scope=='channel':
        restored=set(map(tuple,c.execute('select channel,url from restored_channel_routes where channel=?',(owner,))))
        catalog(c,hidden_channels=inventory)
    else:
        restored=set(map(tuple,c.execute('select channel,url from restored_routes where group_name=?',(owner,))))
        catalog(c,hidden_groups=inventory)
    pending=keys-restored
    if pending-{(r['name'],r['url']) for r in inventory.get(owner,[])}:
        raise ValueError('所选线路已变动，请重新选择')
    if scope=='channel':
        c.executemany('insert into restored_channel_routes values(?,?)',pending)
    else:
        c.executemany('insert into restored_routes values(?,?,?)',[(owner,n,u) for n,u in pending])

def prepare_logo(encoded):
    if not isinstance(encoded,str): raise ValueError('无效的台标文件')
    data=base64.b64decode(encoded,validate=True)
    if not 0<len(data)<=2*1024*1024: raise ValueError('台标请小于或等于 2 MB')
    if data.startswith(b'\x89PNG\r\n\x1a\n'): fmt='png_pipe'
    elif data.startswith(b'\xff\xd8\xff'): fmt='jpeg_pipe'
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP': fmt='webp_pipe'
    else: raise ValueError('请选择 PNG、JPEG 或 WebP 图片')
    try:
        proc=subprocess.run([os.getenv('FFMPEG_BIN','ffmpeg'),'-v','error','-protocol_whitelist','pipe',
            '-f',fmt,'-max_pixels','16777216','-threads','1','-i','pipe:0','-frames:v','1',
            '-vf',"scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease",
            '-c:v','png','-threads','1','-f','image2pipe','pipe:1'],input=data,capture_output=True,timeout=8)
    except (OSError,subprocess.TimeoutExpired) as exc: raise ValueError('台标解码失败，请检查 FFmpeg 或换一张图片') from exc
    if proc.returncode or not proc.stdout.startswith(b'\x89PNG'):raise ValueError('图片无法读取或超过 1600 万像素')
    return proc.stdout

def edit(kind,p):
    if kind=='channel-logo':
        image=prepare_logo(p['image']) if p['image'] is not None else None
    with LOCK,db() as c:
        if job['running'] and kind in ('source','source-state','delete'): raise ValueError('请等待任务结束再修改订阅')
        if kind=='clear-logs':
            category=p.get('kind')
            if category not in ('refresh','probe','history'):raise ValueError('请选择有效的记录类型')
            if c.execute('delete from logs where kind=?',(category,)).rowcount:
                # Deleting older entries can leave max(logs.id) unchanged.
                job['log_epoch']=uuid.uuid4().hex
        elif kind=='settings':
            cfg=config(c)
            hours=hours_value(p['hours'] if 'hours' in p else float(p.get('minutes',cfg['minutes']))/60)
            if p['mode'] not in ('merge','best'): raise ValueError('设置无效')
            method=p.get('probe_mode',cfg['probe_mode'])
            if method not in ('adaptive','continuous','legacy'):raise ValueError('无效检测方式')
            criteria=p.get('criteria',cfg['criteria'])
            speed=float(p.get('min_speed',cfg['min_speed']))
            height=int(p.get('min_height',cfg['min_height']))
            if criteria not in ('speed','resolution','both') or not math.isfinite(speed) or speed<0 or not 1<=height<=16384:
                raise ValueError('测速条件无效')
            cfg.update(criteria=criteria,min_speed=speed,min_height=height)
            if hours!=cfg['minutes']/60:
                c.execute('update sources set next_update=? where enabled and interval_hours is null',(deadline(hours),))
            cfg.update(probe_mode=method,minutes=hours*60,mode=p['mode'],auto_probe=bool(p['auto_probe']),strict=bool(p['strict']))
            save_config(c,cfg)
        elif kind=='source-state':
            if type(p.get('enabled')) is not bool: raise ValueError('订阅状态无效')
            c.execute('begin immediate')
            row=c.execute('select enabled,interval_hours from sources where id=?',(int(p['id']),)).fetchone()
            if not row: raise ValueError('订阅不存在')
            if bool(row['enabled'])!=p['enabled']:
                hours=row['interval_hours'] if row['interval_hours'] is not None else config(c)['minutes']/60
                c.execute('update sources set enabled=?,next_update=? where id=?',(int(p['enabled']),deadline(hours,p['enabled']),int(p['id'])))
        elif kind=='source':
            name=p['name'].strip()
            if not name or p['kind'] not in ('playlist','epg'): raise ValueError('请填写名称并选择类型')
            url=weburl(p['url'].strip())
            old=None
            if p.get('id'):
                old=c.execute('select kind,url,enabled,interval_hours,next_update from sources where id=?',(int(p['id']),)).fetchone()
                if not old: raise ValueError('订阅不存在')
            vals=(name,p['kind'],url,int(p['priority']),int(bool(p.get('enabled',old['enabled'] if old else True))))
            interval=p.get('interval_hours',old['interval_hours'] if old else None)
            if interval is not None:interval=hours_value(interval)
            enabled=vals[-1]
            if old and (interval,enabled)==(old['interval_hours'],old['enabled']):
                due=old['next_update']
            else:
                due=deadline(config(c)['minutes']/60 if interval is None else interval,enabled)
            if old:
                if (old['kind'],old['url'])!=(p['kind'],url):
                    c.execute('delete from eit_events where source_id=?',(p['id'],))
                    c.execute('delete from playlist_epg where source_id=?',(p['id'],))
                    c.execute("update sources set items='[]',xml=null,profiles=null,logos=null,last='',updated=0 where id=?",(p['id'],))
                c.execute('update sources set name=?,kind=?,url=?,priority=?,enabled=?,interval_hours=?,next_update=? where id=?',(*vals,interval,due,p['id']))
                prune_guides(c)
            else: c.execute('insert into sources(name,kind,url,priority,enabled,interval_hours,next_update) values(?,?,?,?,?,?,?)',(*vals,interval,due))
        elif kind=='restore-routes':
            c.execute('begin immediate')
            restore_hidden_routes(c,p.get('scope'),p.get('owner'),p.get('targets'))
        elif kind=='visibility':
            scope,name,url=p['kind'],p['name'],p.get('url','')
            if scope not in ('group','channel','route') or not isinstance(name,str) or not name or not isinstance(url,str) or bool(url)!=(scope=='route') or type(p['hide']) is not bool:
                raise ValueError('无效的隐藏目标')
            key=(scope,name,url)
            c.execute('begin immediate')
            parents=[key for key in ('channel','group') if key in p]
            if parents:
                if len(parents)!=1 or scope!='route' or p['hide']:
                    raise ValueError('无效的单线路恢复请求')
                parent=parents[0]
                restore_hidden_routes(c,parent,p[parent],[p])
                return
            if scope=='channel':
                c.execute('delete from restored_channel_routes where channel=?',(name,))
            if scope=='group':
                c.execute('delete from restored_routes where group_name=?',(name,))
            if p['hide']:
                if c.execute('select 1 from hidden_items where kind=? and name=? and url=?',key).fetchone():return
                channels=catalog(c)
                if scope=='group':exists=any(ch['group']==name for ch in channels)
                else:exists=any(ch['name']==name and (scope=='channel' or any(r['url']==url for r in ch['lines'])) for ch in channels)
                if not exists:raise ValueError('目录已变动，请刷新后重试')
                c.execute('insert into hidden_items values(?,?,?)',key)
            else:
                c.execute('delete from hidden_items where kind=? and name=? and url=?',key)
        elif kind=='delete':
            c.execute('delete from eit_events where source_id=?',(int(p['id']),))
            c.execute('delete from playlist_epg where source_id=?',(int(p['id']),))
            c.execute('delete from sources where id=?',(int(p['id']),))
            prune_guides(c)
        elif kind in ('route','routes','hide-routes'):
            targets=[p] if kind=='route' else p['targets']
            if not isinstance(targets,list) or not targets: raise ValueError('请选择线路')
            c.execute('begin immediate')
            current={(ch['name'],r['url']):r for ch in catalog(c) for r in ch['lines']}
            selected_keys=set()
            for target in targets:
                if not isinstance(target,dict) or not all(isinstance(target.get(k),str) for k in ('name','url')):
                    raise ValueError('无效的线路')
                selected_keys.add((target['name'],target['url']))
            if kind=='hide-routes':
                hidden=set(map(tuple,c.execute("select name,url from hidden_items where kind='route'")))
                if selected_keys-current.keys()-hidden:raise ValueError('频道或线路已变动，请刷新')
                c.executemany("insert or ignore into hidden_items values('route',?,?)",selected_keys)
                return
            mode=p['mode']
            if mode not in ('auto','on','off'): raise ValueError('线路模式无效')
            if selected_keys-current.keys():raise ValueError('频道或线路已变动，请刷新')
            keys={(member,url) for name,url in selected_keys for member in current[name,url]['members']}
            if mode=='auto':
                c.executemany('delete from route_modes where name=? and url=?',keys)
            else:
                c.executemany('insert or replace into route_modes values(?,?,?)',[(*k,mode) for k in keys])
        elif kind in ('channels','hide-channels'):
            names=p['names']
            if not isinstance(names,list) or not names or not all(isinstance(n,str) for n in names) or (kind=='channels' and type(p['enabled']) is not bool):
                raise ValueError('请选择有效的频道和操作')
            c.execute('begin immediate')
            inventory={ch['name']:ch for ch in catalog(c)}
            available=set(inventory)
            if kind=='hide-channels':
                hidden={r[0] for r in c.execute("select name from hidden_items where kind='channel'")}
                if set(names)-available-hidden:raise ValueError('频道已变动，请刷新后重选')
                c.executemany('delete from restored_channel_routes where channel=?',[(name,) for name in set(names)])
                c.executemany("insert or ignore into hidden_items values('channel',?,'')",[(name,) for name in set(names)])
                return
            if not set(names)<=available: raise ValueError('频道已变动，请刷新后重选')
            route_mode=p.get('routes_mode','on' if p['enabled'] else 'off')
            if route_mode is not None:
                if route_mode not in ('on','off') or p['enabled']!=(route_mode=='on'):raise ValueError('线路操作无效')
                keys={(member,r['url']) for name in set(names) for r in inventory[name]['lines'] for member in r['members']}
                c.executemany('insert or replace into route_modes values(?,?,?)',[(*key,route_mode) for key in keys])
                if route_mode=='off':return
            for name in set(names):
                old=c.execute('select value from rules where name=?',(name,)).fetchone()
                rule=json.loads(old[0]) if old else {}
                rule['enabled']=p['enabled']
                c.execute('insert or replace into rules values(?,?)',(name,encode(rule)))
        elif kind=='channel-logo':
            name=p['name']
            if not any(ch['name']==name for ch in catalog(c)):raise ValueError('频道已变更，请刷新后重试')
            row=c.execute('select value from rules where name=?',(name,)).fetchone()
            rule=json.loads(row[0]) if row else {}
            rule.pop('logo',None)
            if image:
                digest=hashlib.sha256(image).hexdigest()
                c.execute('insert or replace into channel_logos values(?,?,?)',(name,digest,image))
                rule['logo']='/logos/'+digest+'.png'
            else:c.execute('delete from channel_logos where name=?',(name,))
            c.execute('insert or replace into rules values(?,?)',(name,encode(rule)))
        elif kind=='channel':
            name=normalize(p['name'])
            value={k:str(p.get(k,'')).strip() for k in ('label','target','epg','logo','group')}
            target=normalize(value['target'])
            value['target']=target if target!=name else ''
            value['enabled']=bool(p.get('enabled',True))
            rules={r[0]:json.loads(r[1]) for r in c.execute('select * from rules')}
            previous=rules.get(name,{})
            if 'logo' not in p:value['logo']=previous.get('logo','')
            value['bound_epg']=bool(value['epg'] and p.get('bound_epg',previous.get('bound_epg',False)))
            if target and target!=name:
                if rules.get(target,{}).get('target') or any(v.get('target')==name for v in rules.values()):
                    raise ValueError('请直接指向最终频道；先解除已有别名链')
            c.execute('insert or replace into rules values(?,?)',(name,encode(value)))
        else: raise ValueError('未知操作')

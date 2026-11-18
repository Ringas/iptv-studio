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
import tempfile
from diagnostics import event, exception as diagnostic_exception, task_context, route_id, probe_details, current_task
from dual_probe import context as probe_context, first_pass, store_cooldown, release_leases, active_states
from time import monotonic as gateway_clock
from email.utils import parsedate_to_datetime
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError
from http.client import HTTPException
from urllib.parse import urlsplit,urljoin
from grouping import programme_profiles
from eit import programmes
from logos import parse_index, image_route, cached_image, cache_is_fresh, prune_cache, cache_versions, MODES
from catalog import classify_result, parse_playlist, playlist_guides, build, selected, guide, xmlroot, xmltv_logos, normalize, assess, speed_display, qualification_policy, policy_changed, activation_assessment

ROOT=Path(__file__).resolve().parent
DATA=Path(os.getenv('DATA_DIR',ROOT/'data'))
LOCK=threading.RLock()
job={'running':False,'message':'空闲','done':0,'total':0,'active_url':None,'sample':None,'revision':0}
probe_process=None
dual_lease_dir=None
DEFAULT={'minutes':480,'auto_probe':True,'strict':True,'mode':'merge','next':0,
         'probe_mode':'adaptive','criteria':'both','min_speed':100,'min_height':720,'logo_beauty':'off'}

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

def extend_gateway(url,seconds):
    # Enrich an already observed 429 with its parsed body without counting a new hit.
    host=urlsplit(url).netloc.lower()
    with gateway_lock:
        last,blocked,opened=gateway_windows.get(host,(0,0,set()))
        gateway_windows[host]=(last,max(blocked,gateway_clock()+seconds),opened)

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
def config(c):
    cfg={**DEFAULT,**json.loads(c.execute("select value from settings").fetchone()[0])}
    # Explicitly saved UI choice wins; environment is only an initial fallback.
    cfg['probe_workers']=2 if str(cfg.get('probe_workers',os.getenv('IPTV_PROBE_WORKERS','1')))=='2' else 1
    return cfg
def retain_legacy_results(c, cfg):
    # Older records lack a stored conclusion; retain the displayed result, not a rule.
    for row in c.execute('select url,result from tests').fetchall():
        test=json.loads(row['result'])
        if test and not test.get('evidence',{}).get('qualification',{}).get('assessment') and 'result_assessment' not in test:
            test['result_assessment']=assess(test,cfg)
            c.execute('update tests set result=? where url=?',(encode(test),row['url']))

def save_config(c, cfg): c.execute("update settings set value=?",(encode(cfg),))
def log(c,text,kind='history',**fields):
    fields.setdefault('task_id',current_task())
    level='WARNING' if fields.get('result') in ('失败','部分失败','未通过','暂时失败','未能验证','待复测') else 'INFO'
    c.execute("insert into logs(ts,message,kind,details) values(?,?,?,?)",
              (int(time.time()),str(text)[:1200],kind,encode(fields)))
    c.execute("delete from logs where kind=? and id < (select id from logs where kind=? order by id desc limit 1 offset 249)",(kind,kind))
    event('record.'+kind,text,level,**fields)

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
        create table if not exists source_refresh_queue(source_id integer primary key,version text not null,queued integer not null);
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
        if 'version' not in {r['name'] for r in c.execute('pragma table_info(sources)')}:
            c.execute("alter table sources add column version text not null default ''")
        for row in c.execute("select id from sources where version=''").fetchall():
            c.execute('update sources set version=? where id=?',(uuid.uuid4().hex,row['id']))
        if 'kind' not in {r['name'] for r in c.execute('pragma table_info(logs)')}:
            c.execute("alter table logs add column kind text not null default 'history'")
            c.execute("alter table logs add column details text not null default '{}'")
        if 'interval_hours' not in {r['name'] for r in c.execute('pragma table_info(sources)')}:
            if not c.in_transaction:c.execute('begin immediate')
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
    subscribed={}
    has_logos=False
    for row in c.execute("select logos from sources where enabled and kind='logo' order by priority,id"):
        has_logos=True
        for key,value in json.loads(row['logos'] or '{}').items():subscribed.setdefault(key,value)
    cfg=config(c)
    channels=build(sources,tests,rules,modes,cfg,profiles,hidden,logos,restored,hidden_groups,channel_routes,hidden_channels,hidden_items,subscribed)
    mode=cfg.get('logo_beauty','off')
    versions=cache_versions([ch['logo_candidates'] for ch in channels],mode,DATA/'logo-cache')
    for channel in channels:
        candidates=channel['logo_candidates']
        channel['original_logo_candidates']=candidates
        if candidates and (has_logos or mode!='off'):
            channel['logo']=image_route(candidates,mode)
            if tuple(candidates) in versions:channel['logo']+='?v='+versions[tuple(candidates)]
            channel['logo_candidates']=[channel['logo']]
    return channels

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

def fetch(url,limit,timeout=20):
    req=Request(weburl(url),headers={'User-Agent':'IPTV-Hub/1.0','Accept-Encoding':'gzip'})
    with build_opener(Redirect()).open(req,timeout=timeout) as response:
        raw=response.read(limit+1)
        base=response.geturl()
    if len(raw)>limit: raise ValueError('订阅超过大小限制')
    if raw.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream: raw=stream.read(limit+1)
        if len(raw)>limit: raise ValueError('解压后超过限制')
    return raw,base

def update_guide(url,attempts,source):
    if url in attempts:
        count,error=attempts[url]
        if error:raise ValueError(error)
        return count
    try:
        raw,base=fetch(url,32*1024*1024)
        root=xmlroot(raw)
        count=len(root.findall('programme'))
        if not count: raise ValueError('XMLTV 没有节目，保留旧快照')
        with LOCK,db() as c:
            require_current_source(c,source)
            c.execute('insert or replace into epg_cache values(?,?,?,?,?)',
                      (url,raw,encode(programme_profiles(root)),encode(xmltv_logos(root,base)),count))
    except StaleSource:raise
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

class StaleSource(ValueError):
    pass


def require_current_source(c,source):
    current=c.execute('select version from sources where id=?',(source['id'],)).fetchone()
    if not current or current['version']!=source['version']:
        raise StaleSource('订阅已变更，忽略旧更新结果')


def route_source_versions(c):
    versions={}
    query="select id,version,items from sources where enabled and kind='playlist'"
    for source in c.execute(query):
        for item in json.loads(source['items']):
            versions.setdefault(item['url'],set()).add((source['id'],source['version']))
    return versions


def enqueue_source_refresh(c,source_id,version):
    c.execute('insert into source_refresh_queue values(?,?,?) on conflict(source_id) do update set version=excluded.version',
              (source_id,version,time.time_ns()))


def queue_source_refresh(source_id):
    if type(source_id) is not int or source_id<1:raise ValueError('请选择一个有效订阅进行更新')
    with LOCK,db() as c:
        row=c.execute('select version from sources where id=?',(source_id,)).fetchone()
        if not row:raise ValueError('订阅不存在')
        enqueue_source_refresh(c,source_id,row['version'])
        job['revision']+=1
    return drain_refresh_queue()==source_id


def drain_refresh_queue(handoff=False):
    with LOCK:
        if job['running'] and not handoff:return
        with db() as c:
            c.execute('delete from source_refresh_queue where source_id not in (select id from sources)')
            c.execute('update source_refresh_queue set version=(select version from sources where id=source_id)')
            row=c.execute('select source_id from source_refresh_queue order by queued,source_id limit 1').fetchone()
        if row:
            start('refresh',source_id=row['source_id'],_handoff=handoff)
            return row['source_id']
        if handoff:job['running']=False


def refresh(source,attempts):
    with LOCK,db() as c:require_current_source(c,source)
    linked_errors=[]
    items=[]
    if source['kind']=='playlist':
        raw,base=fetch(source['url'],32*1024*1024)
        text=raw.decode('utf-8-sig')
        items=parse_playlist(text,base)
        identify_gateway_routes(items,source)
        links=playlist_guides(text,base)
        for i,url in enumerate(links,1):
            try:update_guide(url,attempts,source)
            except Exception as exc:linked_errors.append(f'节目单 {i}：{exc}')
        message=f'成功 · {len(items)} 条'
        if links:message+=f' · 自带节目单 {len(links)-len(linked_errors)}/{len(links)} 份更新成功'
        found=[] if links else collect_eit(source,items)
        if not links:message+=f' · DVB EIT 本次读取 {len(found)} 个频道，{sum(len(e) for _,_,e in found)} 条节目'
    elif source['kind']=='logo':
        raw,base=fetch(source['url'],4*1024*1024)
        logo_index=parse_index(raw,base)
        message=f'成功 · {len(set(logo_index.values()))} 个台标'
    else:
        message=f"成功 · {update_guide(source['url'],attempts,source)} 条"
    with LOCK,db() as c:
        require_current_source(c,source)
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
        if source['kind']=='logo':
            c.execute('update sources set logos=? where id=?',(encode(logo_index),source['id']))
        c.execute('update sources set last=?,updated=? where id=?',(message,int(time.time()),source['id']))
    request_logo_warm(only_missing=True)
    return {item['url'] for item in items},linked_errors

def stop_probe(run_id,url=None):
    with LOCK:
        if not job['running'] or job.get('run_id')!=run_id or not job.get('probing'):
            raise ValueError('本次测速已结束')
        active=active_states
        if active:
            if url is not None and url not in active:raise ValueError('这条线路的测速已结束')
            if url is None:job['stop_queue']=True
            for address,state in active.items():
                if url is None or address==url:
                    state['stop']='已终止' if url is None else '已跳过'
                    if state['process'] is not None:state['process'].kill()
            return
        if url is not None and url!=job['active_url']:
            raise ValueError('这条线路的测速已结束')
        if url is None: job['stop_queue']=True
        if not job['stop_line']:
            job['stop_line']='已终止' if url is None else '已跳过'
        if probe_process is not None: probe_process.kill()

def probe(url,method,options):
    global probe_process
    state=getattr(probe_context,'state',None)
    if urlsplit(url).scheme not in ('http','https'):
        return {'method':method,'quality':'未能验证','status':'未能验证','note':['当前检测器仅支持 HTTP(S) HLS']}
    try:
        args=['node',str(ROOT/'probe.mjs'),url,'--progress']
        if method!='legacy':args.append('--'+method)
        lease_dir=state['directory'] if state else dual_lease_dir
        owner=state['owner'] if state else uuid.uuid4().hex
        gateway_events=[];probe_started=time.monotonic()
        extra={'IPTV_GATEWAY_LEASE_DIR':lease_dir,'IPTV_GATEWAY_OWNER':owner} if lease_dir else {}
        details=dict(task_id=job.get('run_id'),route_id=route_id(url),url=url,method=method,channel=state.get('channel','') if state else job.get('active_channel',''),attempt='recheck' if (state['rechecking'] if state else job.get('rechecking')) else 'initial')
        event('probe.started','开始探测线路','DEBUG',**details)
        last_phase=None
        killed_by_timeout=threading.Event()
        with tempfile.TemporaryFile(mode='w+b') as error_output, subprocess.Popen(args,env={**os.environ,'IPTV_PROBE_OPTIONS':encode(options),'IPTV_PROBE_MANAGED':'1' if method!='legacy' else '0',
                                      'IPTV_PROBE_RECHECK':'1' if (state['rechecking'] if state else job.get('rechecking')) else '0','IPTV_PROBE_MIN_HEIGHT':str(job.get('probe_policy',DEFAULT)['min_height']) if job.get('pipeline') and job.get('probe_policy',DEFAULT)['criteria'] in ('both','resolution') else '0','IPTV_PROBE_POLICY':encode(job.get('probe_policy',DEFAULT)),**extra},stdout=subprocess.PIPE,stderr=error_output,encoding='utf-8') as proc:
            with LOCK:
                if state:state['process']=proc
                else:probe_process=proc
                if (state['stop'] if state else job['stop_line']) or job['stop_queue']:proc.kill()
            def expire():
                killed_by_timeout.set()
                proc.kill()
            timeout=threading.Timer(120 if method in ('continuous','pipeline') else 60,expire)
            timeout.start()
            result=None
            try:
                for line in proc.stdout:
                    sample_event=json.loads(line)
                    if sample_event.get('gateway_acquired') and lease_dir:
                        gateway_events.append({'host':sample_event['host'],'seconds':time.monotonic()-probe_started,'event':'acquired'})
                        continue
                    if sample_event.get('gateway_limit') and lease_dir:
                        gateway_events.append({'host':sample_event['host'],'seconds':time.monotonic()-probe_started,'event':'cooldown','retry_after':sample_event['seconds']})
                        with LOCK:
                            cool_gateway('https://'+sample_event['host'],sample_event['seconds'])
                            store_cooldown(lease_dir,sample_event['host'],gateway_delay('https://'+sample_event['host']))
                        continue
                    if sample_event.get('sample'):
                        phase=sample_event.get('phase')
                        if phase and phase!=last_phase:
                            event('probe.phase','探测阶段变化','DEBUG',**details,phase=phase,seconds=sample_event.get('seconds'))
                            last_phase=phase
                        with LOCK:
                            target=state if state else job
                            target.update(sample=sample_event.get('phase') or speed_display(sample_event.get('speed')),sample_seconds=sample_event.get('seconds',0))
                            if state:
                                job['active_lines'][url].update(sample=target['sample'],sample_seconds=target['sample_seconds'])
                                if job.get('active_url')==url:job.update(sample=target['sample'],sample_seconds=target['sample_seconds'])
                    else: result=sample_event
                if proc.wait()==0 and result is not None:
                    if lease_dir:
                        if result.get('httpStatus')==429:
                            for gateway_event in gateway_events:
                                if gateway_event['event']=='cooldown':
                                    address='https://'+gateway_event['host']
                                    extend_gateway(address,max(gateway_event['retry_after'],result.get('retryAfter') or 0))
                                    store_cooldown(lease_dir,gateway_event['host'],gateway_delay(address))
                        result['gateway_sessions']=gateway_events
                    event('probe.finished','探测结束','DEBUG',**details,elapsed_ms=round((time.monotonic()-probe_started)*1000),measurement=probe_details(result))
                    for trace in (result.get('buffer_estimate') or {}).get('trace',[]):event('probe.buffer','缓冲与分片诊断','DEBUG',**details,diagnostic=trace)
                    return result
            finally:
                timeout.cancel()
                if proc.poll() is None:proc.kill()
                proc.wait()
                error_output.seek(0,2)
                error_output.seek(max(0,error_output.tell()-4000))
                stderr_tail=error_output.read(4000).decode('utf-8',errors='replace')
                if lease_dir:release_leases(lease_dir,owner)
                with LOCK:
                    if state:state['process']=None
                    else:probe_process=None
            event('probe.process_error','探测进程未返回完整结果','DEBUG',**details,exit_code=proc.returncode,timed_out=killed_by_timeout.is_set(),elapsed_ms=round((time.monotonic()-probe_started)*1000),stderr_tail=stderr_tail)
            return {'status':'测速失败','quality':'暂时失败','retryable':True,'note':['检测超过允许时长，未能验证；可稍后重试' if killed_by_timeout.is_set() else '检测组件异常退出，未能验证；持续出现时请开启诊断日志'],'process_error':{'exit_code':proc.returncode,'timed_out':killed_by_timeout.is_set(),'stderr_tail':stderr_tail}}
    except FileNotFoundError: raise RuntimeError('测速需要安装 Node.js 22 或以上')

def progress(message,done,total):
    with LOCK: job.update(message=message,done=done,total=total)

def run(action,requested,source_snapshot=None):
    with LOCK:
        if source_snapshot is None:
            with db() as c:source_snapshot=tuple((r['id'],r['version']) for r in c.execute("select id,version from sources where enabled and kind='playlist'"))
        job.update(running=True,_source_versions=source_snapshot)
    try:return run_task(action,requested)
    finally:
        with LOCK:
            job.pop('_source_versions',None)
            try:drain_refresh_queue(handoff=True)
            except Exception as exc:
                job['running']=False
                diagnostic_exception('task.error','待更新队列暂未启动，保留队列',exc)


@task_context(job)
def run_task(action,requested):
    global dual_lease_dir
    lease_temp=None
    failures=0
    try:
        if action in ('refresh','scheduled'):
            query='select * from sources where enabled'
            args=()
            updated_urls=set()
            guide_attempts={}
            if action=='refresh' and requested is not None:
                query='select * from sources where id=?'
                args=(requested[0],)
            if action=='scheduled':
                query+=' and next_update between 1 and ?'
                args=(int(time.time()),)
            with db() as c: sources=list(c.execute(query+' order by priority,id',args))
            event('refresh.batch','开始更新订阅',source_count=len(sources))
            for i,s in enumerate(sources):
                source_started=time.monotonic()
                event('refresh.started','开始读取订阅','DEBUG',source=s['name'],source_id=s['id'],source_kind=s['kind'],url=s['url'])
                with LOCK:job.update(active_source_id=s['id'],active_source_version=s['version'])
                progress('更新 '+s['name'],i,len(sources))
                try:
                    urls,errors=refresh(s,guide_attempts)
                    updated_urls.update(urls)
                    failures+=bool(errors)
                    with db() as c:
                        row=c.execute('select last from sources where id=?',(s['id'],)).fetchone()
                        log(c,row['last'] if row else '更新完成','refresh',source=s['name'],result='部分失败' if errors else '成功',reason='；'.join(errors),elapsed_ms=round((time.monotonic()-source_started)*1000),updated_routes=len(urls))
                except StaleSource:
                    with db() as c:log(c,'订阅已变更，忽略旧更新结果','refresh',source=s['name'],result='已忽略')
                except Exception as exc:
                    failures+=1
                    with LOCK,db() as c:
                        try:require_current_source(c,s)
                        except StaleSource:continue
                        msg='失败，保留旧快照 · '+str(exc)[:250]
                        c.execute('update sources set last=? where id=?',(msg,s['id']))
                        log(c,'保留旧快照','refresh',source=s['name'],result='失败',reason=str(exc)[:1600],elapsed_ms=round((time.monotonic()-source_started)*1000),error_type=type(exc).__name__,http_status=getattr(exc,'code',None))
                with LOCK,db() as c:
                    c.execute('delete from source_refresh_queue where source_id=? and version=?',(s['id'],s['version']))
                if action=='scheduled':
                    with LOCK,db() as c:
                        hours=s['interval_hours'] if s['interval_hours'] is not None else config(c)['minutes']/60
                        c.execute('update sources set next_update=? where id=? and next_update=? and version=?',(deadline(hours),s['id'],s['next_update'],s['version']))
            with db() as c:prune_guides(c)
        with db() as c: cfg=config(c)
        if action=='probe' or (action=='scheduled' and cfg['auto_probe'] and updated_urls):
            with LOCK,db() as c:
                channels=catalog(c)
                source_versions=route_source_versions(c)
                initial_sources=set(job['_source_versions'])
                source_versions={u:owners if owners<=initial_sources else set() for u,owners in source_versions.items()}
            routes={}
            for ch in channels:
                # Catalog already excludes disabled sources and hidden content.
                # Publication state must not prevent unmeasured/failed routes from recovering.
                for line_number,line in enumerate(ch['lines'],1):
                    url=line['url']
                    if action=='scheduled' and url not in updated_urls: continue
                    info=routes.setdefault(url,{'channels':[], 'sources':[],'options':line.get('options',[]),'deep':False,'line':f'线路{line_number}'})
                    previous=line['test']
                    if previous.get('quality')=='可播但不稳' or previous.get('stalls',0)>=2:
                        info['deep']=True
                    if ch['label'] not in info['channels']: info['channels'].append(ch['label'])
                    for name in line['from']:
                        if name not in info['sources']: info['sources'].append(name)
            targets=list(requested) if requested is not None else list(routes)
            retries={}
            pipeline=cfg['probe_mode']=='pipeline'
            recheck_reasons={}
            passed=failed=uncertain=stopped=skipped=0
            outcomes={}
            with LOCK: job.update(probing=True,total=len(targets),done=0,pipeline=pipeline,probe_policy={k:cfg[k] for k in ('criteria','min_speed','min_height')})
            limited_once={}
            # Opt-in controlled mode; other detection modes keep their existing scheduler.
            dual=pipeline and cfg['probe_workers']==2
            event('probe.batch','准备线路检测',mode=cfg['probe_mode'],workers=2 if dual else 1,route_count=len(targets),policy=job['probe_policy'])
            prefetched={}
            if dual:
                lease_temp=tempfile.TemporaryDirectory(prefix='iptv-gateway-leases-')
                dual_lease_dir=lease_temp.name
                prefetched=first_pass(targets,routes,probe,job,LOCK,gateway_delay,cool_gateway,dual_lease_dir)
                with LOCK:job['active_lines']={}
            i=0
            while i<len(targets):
                u=targets[i]
                with LOCK,db() as c:
                    if not source_versions.get(u) or source_versions.get(u)!=route_source_versions(c).get(u):
                        outcomes[u]='skipped'
                        log(c,'订阅已变更，本轮跳过旧线路','probe',channel=' / '.join(routes.get(u,{}).get('channels',[])),url=u,result='已跳过')
                        job.update(done=i+1,revision=job['revision']+1)
                        i+=1
                        continue
                def wait_for(address):return max(gateway_delay(address),retries[address]-time.monotonic() if address in retries else 0,0)
                if u not in prefetched and wait_for(u)>0:
                    ready=next((k for k in range(i+1,len(targets)) if wait_for(targets[k])<=0),None)
                    if ready is not None:
                        targets[i],targets[ready]=targets[ready],u
                        u=targets[i]
                info=routes.get(u,{'channels':[],'sources':[]})
                channel=' / '.join(info['channels'])
                with LOCK:
                    if job['stop_queue'] and u not in prefetched: break
                    job.update(message=f'测速 {i+1}/{len(targets)} · {channel}',done=i,active_url=u,active_channel=channel,sample=None,sample_seconds=0,stop_line=False,rechecking=u in retries)
                method=cfg['probe_mode']
                if pipeline:method='continuous' if u in retries else 'pipeline'
                if method=='adaptive':
                    method='continuous' if info.get('deep') or u in retries else 'quick'
                cached=None
                try:
                    cached=prefetched.pop(u,None)
                    delay=0 if cached else wait_for(u)
                    if delay:event('probe.wait','等待网关或复测冷却','DEBUG',route_id=route_id(u),channel=channel,wait_seconds=round(delay,2),attempt='recheck' if u in retries else 'initial')
                    while 0<delay<=30 and not job['stop_line']:
                        with LOCK:job['sample']=f'{"间隔复测" if u in retries else "入口冷却"}，约 {math.ceil(delay)} 秒后测试'
                        time.sleep(min(delay,.25))
                        delay=wait_for(u)
                    if not delay and not cached:delay=gateway_delay(u,reserve=True)
                    if cached:
                        result,finished,stop_reason=cached
                        job['stop_line']=stop_reason
                    elif job['stop_line']:result={}
                    elif delay:result={'httpStatus':429,'deferred':True,'note':[f'入口仍在限流冷却，约 {math.ceil(delay)} 秒后可复测']}
                    else:result=probe(u,method,info.get('options',[]))
                except Exception as exc: result={'status':'测速失败','quality':'暂时失败','retryable':True,'note':[str(exc)]}
                is_recheck=bool(job.get('rechecking'))
                event('probe.attempt','处理线路检测结果','DEBUG',route_id=route_id(u),channel=channel,attempt='recheck' if is_recheck else 'initial',cached=bool(cached),**probe_details(result))
                result=classify_result(result)
                if dual and cached and not job['stop_line']:
                    result['dual_initial']=True
                    # Assess fresh evidence, never a historical grace allowance.
                    fresh={k:v for k,v in result.items() if k not in ('grace','stable_qualified')}
                    if assess(fresh,cfg)!='保留' or not result.get('advanced'):
                        result['isolation_required']=True
                    if result.get('retry_deferred')=='gateway_isolation':
                        result.update(inconclusive=True,error_category='resource_contention',retryable=True)
                if result.get('inconclusive') or result.get('httpStatus')==429:
                    result.setdefault('at',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
                    result['evidence']['qualification']={**qualification_policy(cfg),'state':'not_verified','assessment':'未能验证'}
                with LOCK, db() as c:
                    if (not source_versions.get(u) or source_versions.get(u)!=route_source_versions(c).get(u)
                            or not any(r['url']==u for ch in catalog(c) for r in ch['lines'])):
                        outcomes[u]='skipped'
                        log(c,'订阅或线路已变更，保留原检测和启停状态','probe',channel=channel,url=u,result='已跳过')
                        job.update(done=i+1,active_url=None,sample=None,revision=job['revision']+1)
                        i+=1
                        continue
                    fields=dict(channel=channel,line=info.get('line'),source=' / '.join(info['sources']),url=u,route_id=route_id(u),attempt='recheck' if is_recheck else 'initial',diagnostic=probe_details(result))
                    if job['stop_line']:
                        if job['stop_line']=='已跳过': skipped+=1
                        else: stopped+=1
                        outcomes[u]='skipped' if job['stop_line']=='已跳过' else 'stopped'
                        log(c,'已保留上次测速结果','probe',**fields,result=job['stop_line'])
                    elif result.get('httpStatus')==429:
                        reason='；'.join(result.get('note',[]))
                        if not result.get('deferred'):
                            if not result.pop('_entry_cooled',False):cool_gateway(u,result.get('retryAfter'))
                            if u not in retries and gateway_delay(u)<=30:
                                event('probe.retry_scheduled','限流冷却后复测一次','WARNING',route_id=route_id(u),channel=channel,line=info.get('line'),reason=reason,wait_seconds=round(gateway_delay(u),2),http_status=429)
                                limited_once[u]=reason
                                retries[u]=time.monotonic()+gateway_delay(u)
                                targets.append(targets.pop(i))
                                continue
                        uncertain+=1
                        outcomes[u]='uncertain'
                        prior=c.execute('select result from tests where url=?',(u,)).fetchone()
                        kept=json.loads(prior[0]) if prior else {'inconclusive':True,'status':'未能验证'}
                        kept['last_attempt']=result
                        c.execute('insert or replace into tests values(?,?)',(u,encode(kept)))
                        log(c,'上游限流，保留原测速及启停状态','probe',**fields,result='待复测',reason=reason)
                    elif result.get('inconclusive'):
                        if result.get('upstreamCooldown') and result.get('retryAfter'):extend_gateway(u,result['retryAfter'])
                        prior=c.execute('select result from tests where url=?',(u,)).fetchone()
                        kept=json.loads(prior[0]) if prior else {'inconclusive':True,'status':'未能验证'}
                        kept['last_attempt']={**result,'at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
                        c.execute('insert or replace into tests values(?,?)',(u,encode(kept)))
                        if u not in retries and (result.get('retryable') or result.get('isolation_required')) and (result.get('retryAfter') or 0)<=30:
                            recheck_reasons[u]='；'.join(result.get('note',[]))[:500]
                            retries[u]=(finished if dual and cached else time.monotonic())+max(12 if result.get('httpStatus')==607 else 8,result.get('retryAfter') or 0)
                            event('probe.retry_scheduled','安排隔离或异常复测','WARNING',route_id=route_id(u),channel=channel,line=info.get('line'),reason=recheck_reasons[u],wait_seconds=round(max(0,retries[u]-time.monotonic()),2),isolation=bool(result.get('isolation_required')))
                            targets.append(u)
                            job['total']=len(targets)
                        uncertain+=1
                        outcomes[u]='uncertain'
                        log(c,'本次未能验证，保留原测速及启停状态','probe',**fields,result='未能验证',reason='；'.join(result.get('note',[])),error_category=result.get('error_category'))
                    else:
                        if u in limited_once:
                            result.setdefault('note',[]).insert(0,'入口限流冷却后复测；此前响应：'+limited_once[u])
                        result.setdefault('at',time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))
                        prior=c.execute('select result from tests where url=?',(u,)).fetchone()
                        previous=json.loads(prior[0]) if prior else {}
                        if pipeline:
                            result['pipeline']=True
                            if result.get('quality')=='未能验证' and u not in retries:
                                result['retryable']=True
                        direct_history=previous.get('direct_history',[])
                        if not direct_history and previous.get('last_attempt') and u in retries:direct_history=[0]
                        if method!='legacy':
                            result.setdefault('method','continuous' if pipeline else method)
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
                                if was_usable or (previous.get('grace') and policy_changed(previous,cfg) is not True and result['quality']=='未能验证'):
                                    result['grace']=True
                            if u not in retries and (needs_recheck(result,assess(previous,cfg)=='保留') or result.get('isolation_required')):
                                recheck_reasons[u]='；'.join(result.get('note',[]))[:500]
                                retries[u]=(finished if dual and cached else time.monotonic())+max(12 if result.get('httpStatus')==607 else 8,result.get('retryAfter') or 0)
                                event('probe.retry_scheduled','安排隔离或异常复测','WARNING',route_id=route_id(u),channel=channel,line=info.get('line'),reason=recheck_reasons[u],wait_seconds=round(max(0,retries[u]-time.monotonic()),2),isolation=bool(result.get('isolation_required')))
                                targets.append(u)
                                job['total']=len(targets)
                                result.setdefault('note',[]).append('已安排间隔复测，本轮最多复测一次')
                            history=previous.get('recent',[])
                            if u in retries and u not in limited_once:history=history[:-1]
                            result['recent']=(history+[int(result['quality'] in ('流畅','初检可播'))])[-5:]
                        if direct_history:result['direct_history']=direct_history
                        if pipeline:
                            same_policy=policy_changed(previous,cfg) is not True
                            history=previous.get('qualification_history',[]) if same_policy else []
                            if previous.get('qualification_pending'):history=history[:-1]
                            qualifies=assess(result,cfg)=='保留' and result.get('advanced',False)
                            result['qualification_history']=(history+[int(qualifies)])[-3:]
                            result['qualification_pending']=u in targets[i+1:]
                            # Retain a previously qualified route through one independent failure.
                            result['stable_qualified']=qualifies or (same_policy and previous.get('stable_qualified',False) and sum(result['qualification_history'][-2:])>=1)
                            result['grace']=result['stable_qualified'] or result['qualification_pending']
                        result.pop('recheck_control',None)
                        if is_recheck:
                            result['recheck_control']={'assessment':activation_assessment(previous,cfg),'policy':qualification_policy(cfg)}
                            result.setdefault('note',[]).append('本轮异常复测仅更新检测记录，保留复测前启停状态；下一轮首测再判定')
                        assessment=assess(result,cfg)
                        result['evidence']['qualification']={'state':'passed' if assessment=='保留' else 'not_verified' if assessment in ('未能验证','暂时失败','待复测','未测') else 'failed','assessment':assessment,'criteria':cfg['criteria'],'min_speed':cfg['min_speed'],'min_height':cfg['min_height']}
                        reason=[assessment,*result.get('note',[])]
                        if assessment=='速率未达标': reason.append(f"要求严格高于 {cfg['min_speed']} KB/s")
                        if assessment in ('分辨率未达标','分辨率未知'): reason.append(f"要求高度至少 {cfg['min_height']} 像素")
                        quality=result.get('quality')
                        if quality=='可播但不稳' and result.get('stalls',0)>0:quality='易卡顿'
                        metrics=[v for v in (result.get('opening'),quality) if v]
                        if result.get('recheck_control'):metrics.append('异常复测，启停状态保持不变')
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
                        # Every detection entry uses the same first-pass activation policy.
                        if cfg['strict'] and not is_recheck and assessment in ('保留','速率未达标','分辨率未达标'):
                            changed=c.execute('delete from route_modes where url=?',(u,)).rowcount
                            event('route.activation','有效首测恢复自动启停','DEBUG',route_id=route_id(u),channel=channel,assessment=assessment,cleared_manual=changed)
                        else:event('route.activation','保留当前启停','DEBUG',route_id=route_id(u),channel=channel,assessment=assessment,reason='异常复测' if is_recheck else '自动启停关闭或结论未明确')
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
        diagnostic_exception('task.error','后台任务意外中断',exc)
        message='任务中断 · '+str(exc)
    finally:
        dual_lease_dir=None
        if lease_temp:lease_temp.cleanup()
        with LOCK:
            if message.startswith('任务中断'):
                with db() as c: log(c,message)
            if action in ('scheduled','refresh'):job['revision']+=1
            job.update(message=message,probing=False,active_source_id=None,active_url=None,active_channel='',sample=None,active_lines={})

def start(action,url=None,urls=None,source_id=None,_handoff=False):
    with LOCK:
        if job['running'] and not _handoff: raise ValueError('已有任务运行中')
        if action not in ('probe','refresh','scheduled'): raise ValueError('未知任务')
        if source_id is not None:
            if action!='refresh' or url is not None or urls is not None or type(source_id) is not int or source_id<1:
                raise ValueError('请选择一个有效订阅进行更新')
            with db() as c:
                if not c.execute('select 1 from sources where id=?',(source_id,)).fetchone():raise ValueError('订阅不存在')
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
        job.update(source_id=source_id,active_source_id=source_id,run_id=uuid.uuid4().hex,stop_line=False,stop_queue=False,probing=False,running=True,action=action,message='准备任务',done=0,total=0)
        with db() as c:source_snapshot=tuple((r['id'],r['version']) for r in c.execute("select id,version from sources where enabled and kind='playlist'"))
        threading.Thread(target=run,args=(action,[source_id] if source_id is not None else urls,source_snapshot),daemon=True).start()

def scheduler():
    last_error=None
    last_report=0
    next_logo_check=0
    while True:
        time.sleep(5)
        try:
            with LOCK,db() as c:
                if not job['running']:drain_refresh_queue()
                if not job['running'] and c.execute('select 1 from sources where enabled and next_update between 1 and ? limit 1',(int(time.time()),)).fetchone():
                    start('scheduled')
            if time.monotonic()>=next_logo_check and not logo_status()['running']:
                request_logo_warm(only_missing=True,quiet=True)
                next_logo_check=time.monotonic()+300
            if last_error:event('scheduler.recovered','更新计划已恢复运行')
            last_error=None
        except Exception as exc:
            signature=(type(exc).__name__,str(exc))
            if signature!=last_error or time.monotonic()-last_report>=60:
                diagnostic_exception('scheduler.error','更新计划暂时无法执行，请检查数据目录权限和运行空间',exc)
                last_report=time.monotonic()
            last_error=signature


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

# Independent, cancellable logo preparation; line detection keeps priority.
_logo_warm_lock=threading.Lock()
_logo_warm_thread=None
_logo_warm_pending=None
_logo_warm_version=0
_logo_warm_changed=threading.Event()
_logo_progress={'running':False,'phase':'idle','mode':'off','done':0,'total':0,'ready':0,'failed':0,'skipped':0}


def logo_status():
    with _logo_warm_lock:return _logo_progress.copy()


def cleanup_logo_cache():
    with db() as c:channels=catalog(c)
    try:removed=prune_cache([ch['original_logo_candidates'] for ch in channels],DATA/'logo-cache')
    except OSError as exc:
        event('logo.cleanup_failed','台标缓存暂时无法清理，请检查数据目录权限和剩余空间','WARNING',error_type=type(exc).__name__)
        return
    if removed:event('logo.cache_cleaned',f'已清理 {removed} 个未使用的台标缓存文件',log_type='policy')


def request_logo_warm(only_missing=False,quiet=False):
    global _logo_warm_thread,_logo_warm_pending,_logo_warm_version
    with db() as c:
        mode=config(c).get('logo_beauty','off')
        channels=catalog(c)
    prune_cache([ch['original_logo_candidates'] for ch in channels],DATA/'logo-cache',cleanup=False)
    # Several channels may share one image. Count and prepare each candidate set once.
    unique={tuple(ch['original_logo_candidates']):ch for ch in channels if ch['original_logo_candidates']}
    skipped=sum(not ch['original_logo_candidates'] for ch in channels)
    if only_missing:
        if mode=='off':
            cleanup_logo_cache();return
        all_count=len(unique)
        unique={key:ch for key,ch in unique.items() if not cache_is_fresh(key,mode,DATA/'logo-cache')}
        reused=all_count-len(unique)
        if reused and (not quiet or unique):event('logo.cache_reused',f'台标缓存有效 · 复用 {reused} 张 · 待处理 {len(unique)} 张',log_type='policy')
        if not unique:
            cleanup_logo_cache();return
    if mode=='off':unique={}
    with _logo_warm_lock:
        _logo_warm_version+=1
        _logo_warm_pending=(_logo_warm_version,DATA,mode,list(unique.values()))
        _logo_progress.update(running=mode!='off',phase='queued' if mode!='off' else 'off',mode=mode,
                              done=0,total=len(unique),ready=0,failed=0,skipped=skipped,finished_at=0)
        _logo_warm_changed.set()
        if _logo_warm_thread is None:
            _logo_warm_thread=threading.Thread(target=_prepare_logos,daemon=True)
            _logo_warm_thread.start()


def _prepare_logos():
    global _logo_warm_thread,_logo_warm_pending
    while True:
        with _logo_warm_lock:
            task=_logo_warm_pending;_logo_warm_pending=None
            if task is None:
                _logo_warm_thread=None
                return
        version,root,mode,channels=task
        started=time.monotonic();done=ready=failed=0;waiting=False;failures=[]
        label={'off':'关闭','light':'浅色','dark':'深色','transparent':'透明'}[mode]
        if mode=='off':
            event('logo.disabled','台标优化已关闭，保留原图样式',log_type='policy')
            continue
        event('logo.started',f'开始台标检查与优化 · {label} · 共 {len(channels)} 项')
        def local(address):
            with sqlite3.connect(root/'hub.sqlite') as connection:
                row=connection.execute('select png from channel_logos where digest=? limit 1',(address[7:-4],)).fetchone()
            if not row:raise ValueError('没有此上传台标')
            return row[0]
        for channel in channels:
            while True:
                with _logo_warm_lock:
                    if version!=_logo_warm_version:break
                    paused=bool(job.get('running') and job.get('probing'))
                    _logo_progress['phase']='waiting' if paused else 'processing'
                    _logo_warm_changed.clear()
                if paused!=waiting:
                    event('logo.waiting' if paused else 'logo.resumed',
                          '线路检测优先，台标优化等待检测结束' if paused else '线路检测已结束，继续台标优化',log_type='policy')
                    waiting=paused
                if not paused:break
                _logo_warm_changed.wait(.25)
            with _logo_warm_lock:
                if version!=_logo_warm_version:break
            try:
                cached_image(channel['original_logo_candidates'],mode,root/'logo-cache',lambda address,limit:fetch(address,limit,timeout=8),local,migrate=True)
                ready+=1
            except Exception as exc:
                failed+=1
                if len(failures)<3:failures.append(channel['name'])
                event('logo.preparation_failed','台标暂未准备好','DEBUG',channel=channel['name'],error_type=type(exc).__name__)
            done+=1
            with _logo_warm_lock:
                if version!=_logo_warm_version:break
                _logo_progress.update(done=done,ready=ready,failed=failed)
            if done<len(channels) and done%max(10,(len(channels)+3)//4)==0:
                event('logo.progress',f'台标优化 · 已处理 {done}/{len(channels)} 项 · 已就绪 {ready} · 未能处理 {failed}')
        with _logo_warm_lock:
            current=version==_logo_warm_version
            if current:_logo_progress.update(running=False,phase='complete',done=done,ready=ready,failed=failed,finished_at=time.time())
        if not current:
            event('logo.cancelled','设置已变更，停止旧台标队列并切换到最新模式',log_type='policy')
        else:
            if root==DATA:cleanup_logo_cache()
            event('logo.finished',f'台标优化完成 · {label} · 已就绪 {ready}/{len(channels)} 项 · 未能处理 {failed}',
                  elapsed_ms=round((time.monotonic()-started)*1000),log_type='summary',
                  tip=('未能处理：'+'、'.join(failures)+'；保留兜底台标，可稍后重试') if failed else None)


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

def edit(kind,p,queue_refresh=False):
    source_id=None
    warm_logos=False
    if kind=='channel-logo':
        image=prepare_logo(p['image']) if p['image'] is not None else None
    with LOCK,db() as c:
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
            selected=p.get('probe_mode',cfg['probe_mode'])
            method='pipeline' if selected=='pipeline_dual' else selected
            if 'probe_mode' in p:cfg['probe_workers']=2 if selected=='pipeline_dual' else 1
            if method not in ('adaptive','continuous','legacy','pipeline'):raise ValueError('无效检测方式')
            criteria=p.get('criteria',cfg['criteria'])
            speed=float(p.get('min_speed',cfg['min_speed']))
            height=int(p.get('min_height',cfg['min_height']))
            if criteria not in ('speed','resolution','both') or not math.isfinite(speed) or speed<0 or not 1<=height<=16384:
                raise ValueError('测速条件无效')
            if qualification_policy(cfg)!=dict(criteria=criteria,min_speed=speed,min_height=height):
                retain_legacy_results(c,cfg)
            beauty=p.get('logo_beauty',cfg.get('logo_beauty','off'))
            if beauty not in MODES:raise ValueError('台标优化模式无效')
            warm_logos=beauty!='off' or beauty!=cfg.get('logo_beauty','off')
            cfg.update(criteria=criteria,min_speed=speed,min_height=height,logo_beauty=beauty)
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
                c.execute('update sources set enabled=?,next_update=?,version=? where id=?',(int(p['enabled']),deadline(hours,p['enabled']),uuid.uuid4().hex,int(p['id'])))
                if not p['enabled']:c.execute('delete from source_refresh_queue where source_id=?',(int(p['id']),))
        elif kind=='source':
            name=p['name'].strip()
            if not name or p['kind'] not in ('playlist','epg','logo'): raise ValueError('请填写名称并选择类型')
            url=weburl(p['url'].strip())
            old=None
            if p.get('id'):
                old=c.execute('select kind,url,enabled,interval_hours,next_update,version from sources where id=?',(int(p['id']),)).fetchone()
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
                if old['kind']==p['kind']=='epg' and old['url']!=url:
                    c.execute('insert or ignore into epg_cache(url,xml,profiles,logos,programmes) select ?,xml,profiles,logos,programmes from epg_cache where url=?',(url,old['url']))
                if old['kind']!=p['kind']:
                    c.execute('delete from eit_events where source_id=?',(p['id'],))
                    c.execute('delete from playlist_epg where source_id=?',(p['id'],))
                    c.execute("update sources set items='[]',xml=null,profiles=null,logos=null,last='',updated=0 where id=?",(p['id'],))
                c.execute('update sources set name=?,kind=?,url=?,priority=?,enabled=?,interval_hours=?,next_update=? where id=?',(*vals,interval,due,p['id']))
                prune_guides(c)
                source_id=int(p['id'])
            else:
                source_id=c.execute('insert into sources(name,kind,url,priority,enabled,interval_hours,next_update) values(?,?,?,?,?,?,?)',(*vals,interval,due)).lastrowid
            version=old['version'] if old and (old['kind'],old['url'],old['enabled'])==(p['kind'],url,enabled) else uuid.uuid4().hex
            c.execute('update sources set version=? where id=?',(version,source_id))
            if queue_refresh:
                enqueue_source_refresh(c,source_id,version)
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
            c.execute('delete from source_refresh_queue where source_id=?',(int(p['id']),))
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
    if kind in ('source','source-state','delete'):
        with LOCK:job['revision']+=1
    if warm_logos:request_logo_warm()
    elif kind in ('source','source-state','delete','channel','channel-logo','channels','hide-channels','visibility','hide-routes','restore-routes'):
        request_logo_warm(only_missing=True)
    return source_id

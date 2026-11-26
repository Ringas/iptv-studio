"""台标订阅解析和按需渲染；仅使用标准库与已有 FFmpeg。"""
from http.client import HTTPException
from collections import deque
from contextlib import contextmanager
import hashlib
import json
import math
import os
from urllib.parse import quote, urlsplit, urlunsplit
from pathlib import Path
import struct
import subprocess
import threading
import time
import uuid
import zlib
from catalog import logo_keys, logo_url

MODES=('off','light','dark','transparent')
_gate=threading.BoundedSemaphore(2)
_failed={}
_cache_locks={}
_cache_locks_guard=threading.Lock()


def parse_index(raw, base):
    data=json.loads(raw.decode('utf-8-sig'))
    if isinstance(data,dict):
        rows=[{'name':name,'url':value} for name,value in data.items()]
    elif isinstance(data,list): rows=data
    else: raise ValueError('台标订阅须为 JSON 名称映射或频道列表')
    if len(rows)>20000:raise ValueError('台标订阅超过 20000 条')
    result={}
    for row in rows:
        if not isinstance(row,dict):continue
        name=row.get('name');value=row.get('url',row.get('logo'))
        if not isinstance(name,str) or not name.strip() or not isinstance(value,str):continue
        url=logo_url(value,base)
        if not url:continue
        for key in logo_keys(str(row.get('id') or ''),name):result.setdefault(key,url)
    if not result:raise ValueError('台标订阅没有有效条目，保留旧快照')
    return result


def image_route(candidates, mode):
    payload=[candidates,'light-v3'] if mode=='light' else candidates
    digest=hashlib.sha256(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return f'/logos/auto/{digest}/{mode}.png'


def png(width,height,pixels):
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    scan=b''.join(b'\0'+pixels[y*width*4:(y+1)*width*4] for y in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(scan,6))+chunk(b'IEND',b'')


def ffmpeg(data,filters='',raw=False):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):fmt='png_pipe'
    elif data.startswith(b'\xff\xd8\xff'):fmt='jpeg_pipe'
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':fmt='webp_pipe'
    else:raise ValueError('台标须为 PNG、JPEG 或 WebP 图片')
    args=[os.getenv('FFMPEG_BIN','ffmpeg'),'-v','error','-protocol_whitelist','pipe','-f',fmt,'-max_pixels','16777216','-threads','1','-i','pipe:0','-frames:v','1']
    if filters:args+=['-vf',filters]
    args+=['-threads','1']+(['-f','rawvideo','-pix_fmt','rgba'] if raw else ['-c:v','png','-f','image2pipe'])+['pipe:1']
    try:r=subprocess.run(args,input=data,capture_output=True,timeout=8)
    except (OSError,subprocess.TimeoutExpired) as exc:raise ValueError('台标处理失败') from exc
    if r.returncode or not r.stdout:raise ValueError('台标无法解码')
    return r.stdout


def remove_white_background(normalized):
    w,h=struct.unpack('>II',normalized[16:24]);raw=ffmpeg(normalized,raw=True)
    if len(raw)!=w*h*4:raise ValueError('台标像素数据无效')
    def white(i):
        r,g,b,a=raw[i*4:i*4+4];return a>=240 and min(r,g,b)>=240 and max(r,g,b)-min(r,g,b)<=10
    edges=set(range(w))|set(range((h-1)*w,h*w))|{y*w for y in range(h)}|{y*w+w-1 for y in range(h)}
    opaque=sum(a>=240 for a in raw[3::4])/(w*h)
    coverage=sum(white(i) for i in edges)/len(edges)
    if opaque<.95 or coverage<.75:return normalized
    seen={i for i in edges if white(i)};q=deque(seen)
    while q:
        i=q.popleft();x,y=i%w,i//w
        for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            j=ny*w+nx
            if 0<=nx<w and 0<=ny<h and j not in seen and white(j):seen.add(j);q.append(j)
    fraction=len(seen)/(w*h)
    if not .15<=fraction<=.95:return normalized
    result=bytearray(raw)
    for i in seen:result[i*4+3]=0
    # Discard tiny detached remnants of source-image rounded frames at the outer edge only.
    active={i for i in range(w*h) if result[i*4+3]>20};noise=set()
    while active:
        seed=active.pop();component={seed};queue=deque([seed]);touch=False
        while queue:
            i=queue.popleft();x,y=i%w,i//w
            touch=touch or x<max(2,w*.04) or x>w-1-max(2,w*.04) or y<max(2,h*.04) or y>h-1-max(2,h*.04)
            for nx,ny in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
                j=ny*w+nx
                if 0<=nx<w and 0<=ny<h and j in active:active.remove(j);component.add(j);queue.append(j)
        if touch and len(component)<w*h*.01 and all(max(raw[i*4:i*4+3])-min(raw[i*4:i*4+3])<=20 for i in component):noise.update(component)
    for i in noise:result[i*4+3]=0
    return png(w,h,result)

def needs_light_contrast(normalized):
    """Preserve the existing light-mode outline for pale artwork (e.g. CCTV / CGTN)."""
    raw=ffmpeg(normalized,raw=True);weight=0;light=0
    for i in range(0,len(raw),4):
        r,g,b,a=raw[i:i+4]
        if a<=20:continue
        # Ignore a solid white source background; the check runs after conservative removal.
        weight+=a
        if min(r,g,b)>=170 and max(r,g,b)-min(r,g,b)<=65:light+=a
    return bool(weight and light/weight>=.55)


def render(data,mode,prepared=False):
    if mode not in MODES:raise ValueError('台标优化模式无效')
    normalized=data if prepared else ffmpeg(data,"scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease")
    if mode=='off':return normalized
    cleaned=remove_white_background(normalized) if mode=='light' else normalized
    if mode=='light' and not needs_light_contrast(cleaned):
        # Use the approved transparent artwork over a light canvas, without the old outline.
        transparent=render(cleaned,'transparent')
        rgba=ffmpeg(transparent,raw=True);output=bytearray(300*180*4);bg=(237,241,246)
        for i in range(300*180):
            alpha=rgba[i*4+3]/255
            output[i*4:i*4+4]=bytes([round(rgba[i*4+k]*alpha+bg[k]*(1-alpha)) for k in range(3)]+[255])
        return png(300,180,output)
    width,height=struct.unpack('>II',normalized[16:24]);pixels=ffmpeg(normalized,raw=True)
    if len(pixels)!=width*height*4:raise ValueError('台标像素数据无效')
    xs=[];ys=[]
    for y in range(height):
        for x in range(width):
            if pixels[(y*width+x)*4+3]>20:xs.append(x);ys.append(y)
    if not xs:raise ValueError('台标为空白图片')
    x,y=min(xs),min(ys);w,h=max(xs)-x+1,max(ys)-y+1
    scale=min(math.sqrt(18800/(w*h)),236/w,100/h)
    tw,th=max(1,round(w*scale)),max(1,round(h*scale))
    pixels=ffmpeg(normalized,f'crop={w}:{h}:{x}:{y},scale={tw}:{th}:flags=lanczos',True)
    if mode=='transparent':
        output=bytearray(300*180*4);left,top=(300-tw)//2,(180-th)//2
        for sy in range(th):
            start=((top+sy)*300+left)*4
            output[start:start+tw*4]=pixels[sy*tw*4:(sy+1)*tw*4]
        return png(300,180,output)
    # Pale artwork keeps the original light outline; dark mode stays unchanged.
    bg=(237,241,246) if mode=='light' else (37,42,50)
    ink=(48,57,70) if mode=='light' else (225,230,238)
    output=bytearray(bytes((*bg,255))*(300*180));left,top=(300-tw)//2,(180-th)//2
    def blend(dx,dy,color,alpha):
        i=(dy*300+dx)*4
        for k in range(3):output[i+k]=round(color[k]*alpha+output[i+k]*(1-alpha))
    for sy in range(th):
        for sx in range(tw):
            i=(sy*tw+sx)*4;r,g,b,a=pixels[i:i+4]
            contrast=(r+g+b)>660 if mode=='light' else max(r,g,b)<90
            if a and contrast:
                for dx,dy in ((-3,0),(3,0),(0,-3),(0,3),(-2,-2),(-2,2),(2,-2),(2,2),(-1,0),(1,0),(0,-1),(0,1)):
                    blend(left+sx+dx,top+sy+dy,ink,a/255*.85)
    for sy in range(th):
        for sx in range(tw):
            i=(sy*tw+sx)*4;blend(left+sx,top+sy,pixels[i:i+3],pixels[i+3]/255)
    return png(300,180,output)


def request_url(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme,p.netloc,quote(p.path,safe="/%:@!$&'()*+,;=-._~"),quote(p.query,safe="%=&?/:;+,$@!*'()-._~"),''))


# Content-addressed images. URL hashes are locators only, never the image identity.
_index_lock=threading.RLock()
CHECK_INTERVAL=7*24*60*60
RETRY_INTERVAL=60*60


def _url_key(url):return hashlib.sha256(url.encode()).hexdigest()


def _index(folder):
    try:
        value=json.loads((Path(folder)/'index.json').read_text())
        if isinstance(value,dict) and value.get('version')==1 and isinstance(value.get('sources'),dict):return value
    except (OSError,ValueError,TypeError):pass
    value={'version':1,'sources':{}}
    if (Path(folder)/'index.json').exists():value['recovering']=True
    return value


def _atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temp.write_bytes(data);temp.replace(path)
    finally:temp.unlink(missing_ok=True)


def _save_index(folder,index):
    path=Path(folder)/'index.json';data=json.dumps(index,separators=(',',':')).encode()
    try:
        if path.read_bytes()==data:return
    except FileNotFoundError:pass
    _atomic(path,data)


def _original(folder,digest):return Path(folder)/'originals'/(digest+'.png')


def _rendered(folder,digest,mode):
    version='light-v3' if mode=='light' else mode+'-v1'
    return Path(folder)/('content-'+digest+'-'+version+'.png')


def _legacy_path(candidates,mode,folder):
    key=image_route(candidates,mode).split('/')[3]+'-'+mode
    return Path(folder)/(key+'.png')


def _valid_record(folder,record):
    return bool(isinstance(record,dict) and isinstance(record.get('digest'),str) and len(record['digest'])==64
                and all(c in '0123456789abcdef' for c in record['digest'])
                and isinstance(record.get('checked',0),(int,float)) and isinstance(record.get('retry',0),(int,float))
                and _original(folder,record['digest']).is_file())


def cache_revision(folder):
    with _index_lock:return _index(folder).get('generation',0)


def cache_versions(candidate_sets,mode,folder):
    versions={}
    with _index_lock:
        index=_index(folder)
        for candidates in candidate_sets:
            for url in candidates:
                record=index['sources'].get(_url_key(url))
                if not _valid_record(folder,record):continue
                digest=record['digest']
                if not _rendered(folder,digest,mode).exists():digest=record.get('previous',digest)
                if _rendered(folder,digest,mode).exists():versions[tuple(candidates)]=digest
                break
    return versions


def cache_path(candidates,mode,folder):
    with _index_lock:
        index=_index(folder)
        for url in candidates:
            record=index['sources'].get(_url_key(url))
            if _valid_record(folder,record):return _rendered(folder,record['digest'],mode)
    return _legacy_path(candidates,mode,folder)


def cache_is_fresh(candidates,mode,folder):
    now=time.time()
    with _index_lock:
        index=_index(folder)
        for url in candidates:
            record=index['sources'].get(_url_key(url))
            if _failed.get(url,0)>now:
                if _valid_record(folder,record) and _rendered(folder,record['digest'],mode).is_file():return True
                continue
            if not _valid_record(folder,record):return False
            due=now-record.get('checked',0)>=CHECK_INTERVAL
            if due and record.get('retry',0)<=now:return False
            return _rendered(folder,record['digest'],mode).is_file()
    return False


@contextmanager
def cache_lock(path):
    with _cache_locks_guard:
        lock,count=_cache_locks.get(path,(threading.Lock(),0))
        _cache_locks[path]=(lock,count+1)
    try:
        with lock:yield
    finally:
        with _cache_locks_guard:
            _,count=_cache_locks[path]
            if count==1:del _cache_locks[path]
            else:_cache_locks[path]=(lock,count-1)


def _drop_unreferenced(folder,index):
    if index.get('recovering'):return 0  # Unknown references are not proof that an image is unused.
    digests={r['digest'] for r in index['sources'].values() if _valid_record(folder,r)}
    digests.update(r['previous'] for r in index['sources'].values() if r.get('previous'))
    removed=0
    for path in (Path(folder)/'originals').glob('*.png'):
        if path.stem not in digests:path.unlink(missing_ok=True);removed+=1
    for path in Path(folder).glob('content-*.png'):
        if path.name.split('-')[1] not in digests:path.unlink(missing_ok=True);removed+=1
    return removed


def prune_cache(candidate_sets,folder,cleanup=True):
    """Only current catalog references are live; never touch uploads or the real database."""
    folder=Path(folder)
    if not folder.exists():return 0
    candidates=list(candidate_sets);active={_url_key(u) for group in candidates for u in group}
    with _index_lock:
        index=_index(folder)
        legacy=set()
        for group in candidates:
            ready=any(_valid_record(folder,index['sources'].get(_url_key(url))) and any(_rendered(folder,index['sources'][_url_key(url)]['digest'],m).exists() for m in MODES) for url in group)
            if not ready:legacy.update(_legacy_path(group,m,folder).name for m in MODES)
        index['active']=sorted(active)
        if not cleanup:
            _save_index(folder,index);return 0
        if index.get('recovering') and all(_valid_record(folder,index['sources'].get(k)) for k in active):index.pop('recovering',None)
        before=set(index['sources'])
        index['sources']={k:v for k,v in index['sources'].items() if k in active}
        if before!=set(index['sources']):index['generation']=index.get('generation',0)+1
        _save_index(folder,index)
        removed=_drop_unreferenced(folder,index)
        if index.get('recovering'):return removed
        # Old cache entries remain usable during migration if their channel still references them.
        for path in folder.glob('*.png'):
            if not path.name.startswith('content-') and path.name not in legacy:
                path.unlink(missing_ok=True);removed+=1
    return removed


def _source_image(url,folder,fetch,local):
    key=_url_key(url);now=time.time()
    with cache_lock((str(folder),'source',key)):
        with _index_lock:
            record=_index(folder)['sources'].get(key)
            valid=_valid_record(folder,record)
            if valid and (now-record.get('checked',0)<CHECK_INTERVAL or record.get('retry',0)>now):
                return record['digest'],_original(folder,record['digest']).read_bytes()
        try:
            with _gate:
                data=local(url) if url.startswith('/logos/') else fetch(request_url(url),2*1024*1024)[0]
                file_hash=hashlib.sha256(data).hexdigest()
                if valid and record.get('file_hash')==file_hash:
                    with _index_lock:
                        index=_index(folder);current=index['sources'].get(key)
                        if _valid_record(folder,current) and current['digest']==record['digest']:
                            current.update(checked=time.time(),retry=0);_save_index(folder,index)
                            _failed.pop(url,None)
                            return current['digest'],_original(folder,current['digest']).read_bytes()
                with _index_lock:
                    matching=next((r for r in _index(folder)['sources'].values() if isinstance(r,dict) and r.get('file_hash')==file_hash and _valid_record(folder,r)),None)
                    if matching:digest=matching['digest'];normalized=_original(folder,digest).read_bytes()
                if not matching:
                    normalized=ffmpeg(data,"scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease")
                    pixels=ffmpeg(normalized,raw=True)
                    digest=hashlib.sha256(normalized[16:24]+pixels).hexdigest()
            with _index_lock:
                index=_index(folder)
                # A channel can be removed while a network request is in flight: do not resurrect its cache.
                if 'active' in index and key not in index['active']:return digest,normalized
                path=_original(folder,digest)
                if not path.exists():_atomic(path,normalized)
                previous=index['sources'].get(key,{})
                if previous.get('digest')!=digest:index['generation']=index.get('generation',0)+1
                index['sources'][key]={'digest':digest,'checked':time.time(),'retry':0,'file_hash':file_hash}
                old=previous.get('previous') or previous.get('digest')
                if old and old!=digest:index['sources'][key]['previous']=old
                _save_index(folder,index)
            _failed.pop(url,None)
            return digest,normalized
        except (ValueError,OSError,KeyError,HTTPException,subprocess.SubprocessError):
            _failed[url]=now+RETRY_INTERVAL
            if len(_failed)>1024:_failed.pop(next(iter(_failed)))
            if valid:
                with _index_lock:
                    index=_index(folder);current=index['sources'].get(key)
                    if _valid_record(folder,current):
                        current['retry']=time.time()+RETRY_INTERVAL;_save_index(folder,index)
                        return current['digest'],_original(folder,current['digest']).read_bytes()
            raise


def _complete_render(folder,url,digest):
    with _index_lock:
        index=_index(folder);record=index['sources'].get(_url_key(url))
        if record and record.get('digest')==digest:
            previous=record.pop('previous',None)
            if previous:
                _save_index(folder,index)
                referenced=any(previous in (r.get('digest'),r.get('previous')) for r in index['sources'].values())
                if not referenced and not index.get('recovering'):
                    _original(folder,previous).unlink(missing_ok=True)
                    for path in Path(folder).glob('content-'+previous+'-*.png'):path.unlink(missing_ok=True)


def cached_image(candidates,mode,folder,fetch,local,migrate=False,failures=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    legacy=_legacy_path(candidates,mode,folder)
    if not migrate and legacy.exists() and time.time()-legacy.stat().st_mtime<CHECK_INTERVAL:
        with _index_lock:
            known=any(_valid_record(folder,_index(folder)['sources'].get(_url_key(u))) for u in candidates)
            if not known:return legacy.read_bytes()
    for url in candidates:
        if _failed.get(url,0)>time.time():
            # Keep last good content even during the retry cooldown.
            with _index_lock:
                record=_index(folder)['sources'].get(_url_key(url))
                if not _valid_record(folder,record):continue
        try:
            digest,data=_source_image(url,folder,fetch,local)
            path=_rendered(folder,digest,mode)
            with cache_lock((str(folder),'render',digest,mode)):
                with _index_lock:
                    if path.exists():
                        result=path.read_bytes();_complete_render(folder,url,digest);return result
                with _gate:result=render(data,mode,prepared=True)
                with _index_lock:
                    index=_index(folder)
                    # Store only if a live source still references this content.
                    if any(r.get('digest')==digest for r in index['sources'].values()):
                        _atomic(path,result);index['generation']=index.get('generation',0)+1;_save_index(folder,index)
                    _complete_render(folder,url,digest)
                return result
        except (ValueError,OSError,KeyError,HTTPException,subprocess.SubprocessError) as exc:
            if failures is not None: failures.append((url,exc))
            with _index_lock:
                record=_index(folder)['sources'].get(_url_key(url),{})
                if record.get('previous'):
                    old=_rendered(folder,record['previous'],mode)
                    if old.exists():return old.read_bytes()
            continue
    # Existing processed images survive a temporary source outage during the upgrade.
    legacy=_legacy_path(candidates,mode,folder)
    if legacy.exists():return legacy.read_bytes()
    raise ValueError('没有可加载的台标')

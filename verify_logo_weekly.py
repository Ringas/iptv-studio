"""按内容去重、每周校验、无变化不重渲染、失败回退与引用清理。无真实数据/网络。"""
import tempfile,time,struct,zlib,threading
from pathlib import Path
from collections import Counter
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import logos

def picture(color):return logos.png(20,12,bytes((*color,255))*240)
def metadata(png):
 data=b'Comment\0metadata changed';kind=b'tEXt'
 return png[:-12]+struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)+png[-12:]
clock=[time.time()];hits=Counter();a='https://fixture.invalid/a';b='https://other.invalid/b';c='https://new.invalid/c'
images={a:picture((20,70,130)),b:metadata(picture((20,70,130))),c:picture((20,70,130))};broken=set()
def fetch(url,*_):
 hits[url]+=1
 if url in broken:raise OSError('temporary fixture failure')
 return images[url],url
with tempfile.TemporaryDirectory() as d,patch.object(logos.time,'time',side_effect=lambda:clock[0]):
 folder=Path(d);render_original=logos.render
 with patch.object(logos,'render',wraps=render_original) as render:
  first=logos.cached_image([a],'dark',folder,fetch,None);one=logos.cache_path([a],'dark',folder)
  assert render.call_count==1 and hits[a]==1
  assert logos.cached_image([b],'dark',folder,fetch,None)==first
  assert logos.cache_path([b],'dark',folder)==one and render.call_count==1,'same decoded image under different URLs was rendered twice'
  assert len(list((folder/'originals').glob('*.png')))==1
  before=(one.read_bytes(),one.stat().st_mtime_ns);clock[0]+=logos.CHECK_INTERVAL-1
  assert logos.cached_image([a],'dark',folder,fetch,None)==first and hits[a]==1
  clock[0]+=1
  assert not logos.cache_is_fresh([a],'dark',folder)
  with patch.object(logos,'ffmpeg',side_effect=AssertionError('unchanged file was decoded again')):
   assert logos.cached_image([a],'dark',folder,fetch,None)==first
  assert hits[a]==2 and render.call_count==1 and before==(one.read_bytes(),one.stat().st_mtime_ns),'unchanged weekly check rewrote cache'
  clock[0]+=logos.CHECK_INTERVAL;images[a]=picture((140,50,40))
  second=logos.cached_image([a],'dark',folder,fetch,None);two=logos.cache_path([a],'dark',folder)
  assert second!=first and two!=one and one.exists(),'shared old content must stay for the other channel'
  logos.prune_cache([[a]],folder)
  assert not one.exists() and two.exists(),'unreferenced old content not cleaned'
  # Address-only changes identify existing content without rerendering; delay cleanup until identification.
  images[c]=images[a];logos.prune_cache([[c]],folder,cleanup=False);count=render.call_count
  with patch.object(logos,'ffmpeg',side_effect=AssertionError('identical bytes under a new address were decoded twice')):
   assert logos.cached_image([c],'dark',folder,fetch,None)==second and render.call_count==count
  logos.prune_cache([[c]],folder);assert two.exists() and logos.cache_path([c],'dark',folder)==two
  # Weekly failure retains usable pixels, records a bounded retry, and avoids request storms.
  clock[0]+=logos.CHECK_INTERVAL;broken.add(c);count=hits[c]
  assert logos.cached_image([c],'dark',folder,fetch,None)==second and hits[c]==count+1
  assert logos.cache_is_fresh([c],'dark',folder)
  clock[0]+=logos.RETRY_INTERVAL-1
  assert logos.cached_image([c],'dark',folder,fetch,None)==second and hits[c]==count+1
  clock[0]+=1;broken.remove(c);assert logos.cached_image([c],'dark',folder,fetch,None)==second and hits[c]==count+2
  # Successful download followed by render failure must retain the last good processed image.
  clock[0]+=logos.CHECK_INTERVAL;images[c]=picture((30,140,40))
  with patch.object(logos,'render',side_effect=ValueError('render fixture failure')):
   assert logos.cached_image([c],'dark',folder,fetch,None)==second and two.exists()
  third=logos.cached_image([c],'dark',folder,fetch,None);assert third!=second and not two.exists()
  logos.prune_cache([],folder);assert not list(folder.glob('content-*.png')) and not list((folder/'originals').glob('*.png'))
# A removal during a download must not resurrect unused files.
with tempfile.TemporaryDirectory() as d:
 folder=Path(d);logos.prune_cache([[a]],folder) if folder.exists() else None
 entered=threading.Event();release=threading.Event()
 def slow(*_):entered.set();assert release.wait(5);return picture((20,70,130)),a
 with ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(logos.cached_image,[a],'dark',folder,slow,None);assert entered.wait(3)
  logos.prune_cache([],folder);release.set();assert future.result(timeout=5).startswith(b'\x89PNG')
 assert not list(folder.glob('content-*.png')) and not list((folder/'originals').glob('*.png'))
# Legacy cache survives offline migration, then retires after a verified replacement.
with tempfile.TemporaryDirectory() as d:
 folder=Path(d);old=logos._legacy_path([a],'dark',folder);old.write_bytes(first)
 with patch.object(logos,'_failed',{}):
  assert logos.cached_image([a],'dark',folder,lambda *_:(_ for _ in ()).throw(OSError()),None)==first
  logos.prune_cache([[a]],folder);assert old.exists()
  logos.clear_failures();logos.cached_image([a],'dark',folder,fetch,None,migrate=True);logos.prune_cache([[a]],folder);assert not old.exists()
# Broken metadata cannot justify deleting otherwise valid cached files.
with tempfile.TemporaryDirectory() as d:
 folder=Path(d);(folder/'originals').mkdir();(folder/'originals'/('a'*64+'.png')).write_bytes(first)
 cached=folder/('content-'+'a'*64+'-dark-v1.png');cached.write_bytes(first)
 (folder/'index.json').write_text('{broken')
 logos.prune_cache([[a]],folder);assert cached.exists(),'corrupt metadata caused destructive pruning'
# Palette + partial-alpha inputs retain the exact established four-mode pixels.
def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
palette=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,3,0,0,0))+chunk(b'PLTE',bytes((240,130,20,60,180,50)))+chunk(b'tRNS',bytes((40,128)))+chunk(b'IDAT',zlib.compress(bytes((0,0,1,0,1,0))))+chunk(b'IEND',b'')
with tempfile.TemporaryDirectory() as d:
 for mode in logos.MODES:
  out=logos.cached_image(['https://fixture.invalid/palette'],mode,d,lambda *_:(palette,''),None)
  assert logos.ffmpeg(out,raw=True)==logos.ffmpeg(logos.render(palette,mode),raw=True),mode
assert not logos._cache_locks
print('通过：跨地址/元数据内容去重、七天边界、未变化永久复用、变化更新、共享引用保护、失败旧图保留、重试退避、删除竞态与离线迁移。')

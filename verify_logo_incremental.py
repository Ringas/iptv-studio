"""台标订阅增量缓存：真实 HTTP、逐图失效、TTL、其他模式/失败快照保留。"""
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from collections import Counter
import tempfile,threading,json,time,os
import service,logos

class Fixture(BaseHTTPRequestHandler):
 hits=Counter();changed=False;invalid=False;broken=False;color=20
 def log_message(self,*_):pass
 def do_GET(self):
  self.hits[self.path]+=1
  if self.path=='/index':
   content={} if self.invalid else {'CCTV1':base+('/a2.png' if self.changed else '/a.png'),'CGTN':base+'/b.png','江苏卫视':base+'/a.png'}
   body=json.dumps(content).encode();mime='application/json'
  elif self.broken and self.path=='/b.png':self.send_response(503);self.end_headers();return
  else:
   body=logos.png(12,8,bytes((self.color,60,150,255))*96);mime='image/png'
  self.send_response(200);self.send_header('Content-Type',mime);self.end_headers();self.wfile.write(body)

with tempfile.TemporaryDirectory() as d,patch.object(service,'DATA',Path(d)):
 service.init();root=Path(d);folder=root/'logo-cache';server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
 base=f'http://127.0.0.1:{server.server_port}';threading.Thread(target=server.serve_forever,daemon=True).start()
 def join():
  worker=service._logo_warm_thread
  if worker:worker.join(20);assert not worker.is_alive()
 def files():return {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in folder.glob('*.png')}
 try:
  with service.db() as c:
   items=[dict(name=n,url=base+'/live/'+str(i),attrs={'tvg-logo':base+'/fallback.png'},options=[]) for i,n in enumerate(['CCTV1','CGTN','江苏卫视'])]
   c.execute("insert into sources(name,kind,enabled,priority,items) values('fixture','playlist',1,1,?)",(service.encode(items),))
   cfg=service.config(c);cfg.update(minutes=0,logo_beauty='dark');service.save_config(c,cfg)
   c.execute("insert into sources(name,kind,url,enabled,priority) values('logo','logo',?,1,1)",(base+'/index',))
   source=dict(c.execute("select * from sources where kind='logo'").fetchone())
  original_render=logos.render
  with patch.object(logos,'render',wraps=original_render) as render:
   service.refresh(source,{});join();assert render.call_count==1,'same image under different URLs was not deduplicated'
   with service.db() as c:channels=service.catalog(c)
   # A second rendering mode must remain intact across subscription refreshes.
   logos.cached_image(channels[0]['original_logo_candidates'],'transparent',folder,lambda u,l:service.fetch(u,l),lambda _:None)
   before=files();count=render.call_count;hits=Fixture.hits.copy();version=service._logo_warm_version
   service.refresh(source,{});join()
   assert files()==before and render.call_count==count and service._logo_warm_version==version
   assert all(Fixture.hits[k]==v for k,v in hits.items() if k!='/index'),'unchanged refresh downloaded pictures'
   # Only one changed mapping gets a new cache key; no valid or other-mode cache is deleted.
   Fixture.changed=True;service.refresh(source,{});join()
   assert render.call_count==count and Fixture.hits['/a2.png']==1,'new address with unchanged image was rerendered'
   after=files();assert all(after.get(k)==v for k,v in before.items())
   # Identical URL content changes are deferred until TTL, then only that image refreshes.
   with service.db() as c:channels=service.catalog(c)
   target=next(ch for ch in channels if ch['name']=='CCTV1综合');key=logos.cache_path(target['original_logo_candidates'],'dark',folder)
   previous=key.read_bytes();Fixture.color=100;count=render.call_count
   service.refresh(source,{});join();assert render.call_count==count and key.read_bytes()==previous
   with logos._index_lock:
    index=logos._index(folder);index['sources'][logos._url_key(base+'/a2.png')]['checked']=time.time()-logos.CHECK_INTERVAL-1;logos._save_index(folder,index)
   unchanged=files()
   service.refresh(source,{});join();assert render.call_count==count+1 and logos.cache_path(target['original_logo_candidates'],'dark',folder).read_bytes()!=previous
   after=files();assert all(after.get(k)==v for k,v in unchanged.items())
   # Empty/invalid index cannot destroy the last mapping or any cached image.
   Fixture.invalid=True;before=files()
   with service.db() as c:old=c.execute('select logos from sources where id=?',(source['id'],)).fetchone()[0]
   try:service.refresh(source,{});raise AssertionError('empty index accepted')
   except ValueError:pass
   with service.db() as c:assert c.execute('select logos from sources where id=?',(source['id'],)).fetchone()[0]==old
   assert files()==before;Fixture.invalid=False
   # Temporary image errors retain the old file and do not clear unrelated failure cooldowns.
   candidates=[base+'/b.png'];old=logos.cached_image(candidates,'dark',folder,lambda u,l:service.fetch(u,l),lambda _:None)
   path=logos.cache_path(candidates,'dark',folder)
   with logos._index_lock:
    index=logos._index(folder);index['sources'][logos._url_key(base+'/b.png')]['checked']=time.time()-logos.CHECK_INTERVAL-1;logos._save_index(folder,index)
   Fixture.broken=True;logos._failed[base+'/unrelated.png']=time.time()+30
   assert logos.cached_image(candidates,'dark',folder,lambda u,l:service.fetch(u,l),lambda _:None)==old
   service.refresh(source,{});join();assert base+'/unrelated.png' in logos._failed and path.read_bytes()==old
 finally:
  join();server.shutdown();logos.clear_failures()
# Foreground and background callers must share an in-flight cache miss.
from concurrent.futures import ThreadPoolExecutor
with tempfile.TemporaryDirectory() as folder:
 barrier=threading.Barrier(3);downloads=[]
 image=logos.png(12,8,bytes((30,80,130,255))*96)
 def download(*_):
  downloads.append(True);time.sleep(.1);return image,'https://fixture.invalid/logo.png'
 def caller():
  barrier.wait();return logos.cached_image(['https://fixture.invalid/logo.png'],'dark',folder,download,lambda _:None)
 with patch.object(logos,'render',wraps=logos.render) as render,ThreadPoolExecutor(max_workers=2) as pool:
  one=pool.submit(caller);two=pool.submit(caller);barrier.wait()
  assert one.result(timeout=10)==two.result(timeout=10)
  assert len(downloads)==1 and render.call_count==1,'concurrent duplicate download/render'
 assert not logos._cache_locks,'per-image lock leaked'
print('通过：未变更订阅零图片下载/零重渲染，单图变化只处理一张，其他模式缓存保留，同地址每周校验更新，失败保留旧图与旧快照、并发请求只处理一次。')

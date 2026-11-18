"""Run both published images against identical synthetic HLS and actual browser UI."""
import functools,json,os,shutil,subprocess,sys,threading,time
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.request import build_opener,ProxyHandler

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
media=out/'media';media.mkdir()
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=1280x720:rate=25','-t','10','-c:v','libx264','-preset','veryfast','-b:v','1800k','-g','50','-sc_threshold','0','-pix_fmt','yuv420p','-f','hls','-hls_time','2','-hls_list_size','0','-hls_playlist_type','vod',str(media/'stream.m3u8')],check=True)
class Media(SimpleHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  time.sleep(.04) # Identical deterministic upstream latency for both images.
  super().do_GET()
server=ThreadingHTTPServer(('127.0.0.1',18880),functools.partial(Media,directory=str(media)))
threading.Thread(target=server.serve_forever,daemon=True).start()
op=build_opener(ProxyHandler({}));names=[];chrome=None
try:
 for label,image,port in [('baseline','talktoo/iptv-studio:v1.1.3',18881),('candidate','talktoo/iptv-studio:slim-test-55f8cf7',18882)]:
  subprocess.run(['docker','pull',image],check=True)
  name='iptv-first-frame-'+label;names.append(name)
  subprocess.run(['docker','run','-d','--init','--name',name,'--network','host','--tmpfs','/app/data:uid=1000,gid=1000','-e',f'PORT={port}','-e','LOG_LEVEL=WARNING',image],check=True)
  for i in range(60):
   try:
    with op.open(f'http://127.0.0.1:{port}/health',timeout=1):break
   except OSError:time.sleep(.2)
  else:raise RuntimeError('app boot timeout')
  code="""import service
service.init()
with service.db() as c:
 cfg=service.config(c);cfg.update(minutes=0,auto_probe=False);service.save_config(c,cfg)
 item={'name':'CCTV1模拟样本','url':'http://127.0.0.1:18880/stream.m3u8','attrs':{},'options':[]}
 c.execute("insert into sources(name,kind,enabled,priority,items) values('synthetic','playlist',1,1,?)",(service.encode([item]),))
"""
  subprocess.run(['docker','exec',name,'python3','-c',code],check=True)
 chrome_bin=next((shutil.which(x) for x in ('google-chrome','google-chrome-stable','chromium','chromium-browser') if shutil.which(x)),None)
 if not chrome_bin:raise RuntimeError('Chrome is required')
 with (out/'chrome.log').open('w') as log:
  chrome=subprocess.Popen([chrome_bin,'--headless=new','--no-sandbox','--disable-dev-shm-usage','--autoplay-policy=no-user-gesture-required','--remote-debugging-port=19222',f'--user-data-dir={out}/chrome-profile','about:blank'],stdout=log,stderr=log)
  for _ in range(60):
   try:
    with op.open('http://127.0.0.1:19222/json/version',timeout=1):break
   except OSError:time.sleep(.2)
  else:raise RuntimeError('Chrome boot timeout')
  subprocess.run(['node','scripts/benchmark_player.cjs','http://127.0.0.1:19222',str(out/'first-frame.json')],check=True,timeout=650)
finally:
 if chrome:chrome.terminate();chrome.wait(timeout=10)
 for name in names:
  log=subprocess.run(['docker','logs',name],capture_output=True);(out/(name+'.log')).write_bytes(log.stdout+log.stderr)
  subprocess.run(['docker','rm','-fv',name],check=False)
 server.shutdown();server.server_close()

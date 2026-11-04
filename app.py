import base64
import hmac
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import time
import secrets
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from service import ROOT,DATA,LOCK,job,init,db,config,catalog,epg,selected,edit,start,scheduler,stop_probe
from catalog import export,xmltv_stamp
from catalog import xmlroot
import webplay

HOST=os.getenv('BIND','127.0.0.1')
PORT=int(os.getenv('PORT','8788'))
PASSWORD=os.getenv('ADMIN_PASSWORD','')
LAN_NO_PASSWORD=os.getenv('LAN_NO_PASSWORD','1')=='1'
PUBLIC_ACCESS=os.getenv('ALLOW_PUBLIC_ACCESS')=='1'
AUTH_KEY=secrets.token_bytes(32)
verified_login=None
LAN_RANGES=tuple(map(ipaddress.ip_network,('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','169.254.0.0/16','fc00::/7','fe80::/10')))
FORMATS={'m3u':'application/vnd.apple.mpegurl','txt':'text/plain','json':'application/json','xml':'application/xml'}

def lan_address(value):
    try:
        ip=ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip=ip.ipv4_mapped
    return ip.is_loopback or any(ip in network for network in LAN_RANGES)

class Handler(BaseHTTPRequestHandler):
    def send(self,code,body,ctype,filename=None):
        if isinstance(body,str): body=body.encode()
        self.send_response(code)
        self.send_header('Content-Type',ctype if ctype=='image/png' else ctype+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        if filename: self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers()
        self.wfile.write(body)

    def local_request(self):
        host=urlsplit('//'+self.headers.get('Host','')).hostname
        return lan_address(self.client_address[0]) and (host=='localhost' or lan_address(host))

    def allow_network(self):
        if PUBLIC_ACCESS or self.local_request():
            return True
        self.send(403,'请使用 NAS 的局域网 IP 访问','text/plain')
        return False

    def auth(self,connection=None):
        global verified_login
        if connection is None:
            with db() as c: return self.auth(c)
        record=None if PASSWORD else connection.execute('select salt,verifier from admin_access where id=1').fetchone()
        if PASSWORD or record:
            header=self.headers.get('Authorization','')
            valid=False
            if header.startswith('Basic '):
                try:
                    user,separator,secret=base64.b64decode(header[6:],validate=True).partition(b':')
                except ValueError:
                    user=separator=secret=b''
                if user==b'admin' and separator and secret:
                    if PASSWORD:
                        valid=hmac.compare_digest(secret,PASSWORD.encode())
                    else:
                        salt,verifier=record
                        # Reuse a successful check for browser polling; changing the record invalidates it.
                        stamp=hmac.digest(AUTH_KEY,verifier+secret,'sha256')
                        valid=hmac.compare_digest(stamp,verified_login or b'')
                        if not valid:
                            valid=hmac.compare_digest(hashlib.pbkdf2_hmac('sha256',secret,salt,600000),verifier)
                            if valid: verified_login=stamp
            if not valid:
                self.send_response(401)
                self.send_header('WWW-Authenticate','Basic realm="IPTV Hub", charset="UTF-8"')
                self.end_headers()
                return False
        elif not self.local_request():
            self.send(403,'请先在局域网设置管理密码，再从公网访问管理台','text/plain')
            return False
        return True

    def do_GET(self):
        url=urlsplit(self.path)
        path=url.path
        try:
            if not self.allow_network(): return
            if path=='/health':
                self.send(200,'ok','text/plain'); return
            if re.fullmatch(r'/logos/[0-9a-f]{64}\.png',path):
                with db() as c: row=c.execute('select png from channel_logos where digest=? limit 1',(path[7:-4],)).fetchone()
                if row:self.send(200,row[0],'image/png')
                else:self.send(404,'没有此台标','text/plain')
                return
            if path in ('/live.m3u','/live.txt','/live.json','/guide.xml'):
                kind=path.split('.')[-1]
                with db() as c:
                    c.execute('begin')
                    channels=list(selected(catalog(c),config(c)))
                    base=os.getenv('PUBLIC_BASE_URL') or 'http://'+self.headers['Host']
                    for ch in channels:
                        if ch['logo'].startswith('/logos/'):
                            ch['logo']=base.rstrip('/')+ch['logo']
                            ch['logo_candidates']=[ch['logo']]
                    content=epg(c,channels) if kind=='xml' else export(kind,channels,base.rstrip('/')+'/guide.xml')
                view=parse_qs(url.query).get('view',[''])[0] if kind=='m3u' else ''
                ctype=FORMATS[kind]
                if kind=='m3u' and view!='download':
                    accept=self.headers.get('Accept','').lower()
                    if view=='text' or 'text/html' in accept or ctype not in accept:
                        ctype='text/plain'
                self.send(200,content,ctype,'channels.m3u' if view=='download' else None); return
            if not self.auth(): return
            if path=='/api/play-media':
                try: webplay.serve(self,parse_qs(url.query))
                except (BrokenPipeError,ConnectionResetError): pass
                except Exception as exc:
                    webplay.record_error(parse_qs(url.query),exc)
                    self.send(502,webplay.upstream_error(exc),'text/plain')
                return
            if path=='/api/play-status':
                self.send(200,json.dumps({'error':webplay.last_error(parse_qs(url.query).get('session',[''])[0])}),'application/json')
                return
            if path in ('/','/ui.js','/style.css','/hls.min.js'):
                file=ROOT/({'/':'index.html','/ui.js':'ui.js','/style.css':'style.css','/hls.min.js':'hls.min.js'}[path])
                self.send(200,file.read_bytes(),mimetypes.guess_type(file.name)[0] or 'text/plain'); return
            with db() as c:
                c.execute('begin')
                if path=='/api/state':
                    hidden_groups={}
                    hidden_channels={}
                    hidden_items=[dict(r) for r in c.execute('select kind,name,url from hidden_items')]
                    channels=catalog(c,hidden_groups,hidden_channels,hidden_items)
                    settings=config(c)
                    settings['next']=c.execute('select min(next_update) from sources where enabled and next_update>0').fetchone()[0] or 0
                    active=list(selected(channels,settings))
                    sources=[dict(r) for r in c.execute("select id,name,kind,url,priority,enabled,last,updated,interval_hours,next_update from sources order by priority,id")]
                    linked={}
                    for row in c.execute('select source_id,url from playlist_epg order by rowid'):
                        linked.setdefault(row['source_id'],[]).append(row['url'])
                    for source in sources:source['guide_urls']=linked.get(source['id'],[])
                    res={'preview':os.getenv('STUDIO_PREVIEW')=='1','settings':settings,'channels':channels,'exported':len(active),'lines':sum(len(ch['lines']) for ch in active),
                         'sources':sources,'password_mode':'deployment' if PASSWORD else 'enabled' if c.execute('select 1 from admin_access').fetchone() else 'off',
                         'hidden_groups':hidden_groups,'hidden_channels':hidden_channels,
                         'hidden':hidden_items,
                         'rules':{r[0]:json.loads(r[1]) for r in c.execute('select * from rules')}}
                elif path=='/api/status':
                    with LOCK: current=job.copy()
                    res={'job':current,'log_id':c.execute('select max(id) from logs').fetchone()[0]}
                    if 'compact' not in parse_qs(url.query):
                        res['logs']=[dict(r) for r in c.execute('select id,ts,message from logs order by id desc limit 25')]
                elif path=='/api/logs':
                    kind=parse_qs(url.query).get('kind',['refresh'])[0]
                    res=[{**dict(r),**json.loads(r['details'])} for r in c.execute('select id,ts,message,details from logs where kind=? order by id desc limit 250',(kind,))]
                elif path=='/api/guide-options':
                    guides={}
                    for row in c.execute('select xml from epg_cache join active_epg using(url) order by priority,embedded,owner,position'):
                        root=xmlroot(row[0])
                        for ch in root.findall('channel'):
                            if ch.get('id'):guides.setdefault(ch.get('id'),ch.findtext('display-name') or ch.get('id'))
                        for prog in root.iterfind('programme'):
                            if prog.get('channel'):guides.setdefault(prog.get('channel'),prog.get('channel'))
                    for row in c.execute('select distinct e.name from eit_events e join sources s on s.id=e.source_id where s.enabled and e.stop>?',(int(time.time()),)):
                        guides.setdefault(row[0],row[0])
                    res=sorted(guides.items(),key=lambda item:(item[1],item[0]))
                elif path=='/api/guide':
                    channel=parse_qs(url.query).get('channel',[''])[0]
                    chosen=[ch for ch in catalog(c) if ch['name']==channel]
                    root=ET.fromstring(epg(c,chosen))
                    res=sorted([{'start':n.get('start'),'timestamp':xmltv_stamp(n.get('start')),'stop':n.get('stop'),'title':n.findtext('title','')} for n in root.findall('programme')],key=lambda v:v['timestamp'] or 0)
                else:
                    self.send(404,'没有此页面','text/plain');return
            self.send(200,json.dumps(res,ensure_ascii=False),'application/json')
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception as exc: self.send(500,str(exc),'text/plain')

    def do_POST(self):
        try:
            if not self.allow_network() or not self.auth(): return
            origin=self.headers.get('Origin')
            if origin and urlsplit(origin).netloc!=self.headers.get('Host'):
                self.send(403,'请求来源不匹配','text/plain');return
            if self.headers.get_content_type()!='application/json': raise ValueError('需要 JSON 请求')
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=(2800000 if self.path=='/api/channel-logo' else 262144): raise ValueError('请求大小无效')
            payload=json.loads(self.rfile.read(n))
            if not isinstance(payload,dict): raise ValueError('无效请求')
            if self.path=='/api/play':
                with db() as c:
                    line=next((r for ch in catalog(c) if ch['name']==payload.get('name') for r in ch['lines'] if r['url']==payload.get('url')),None)
                if line is None: raise ValueError('没有此频道线路，请刷新频道目录')
                try: result=webplay.new_session(line)
                except Exception as exc:
                    self.send(502,webplay.upstream_error(exc),'text/plain');return
                self.send(200,json.dumps(result),'application/json');return
            elif self.path=='/api/close-play':
                webplay.close_session(str(payload.get('session','')))
            elif self.path=='/api/access-password':
                if PASSWORD:
                    self.send(403,'访问密码由部署配置管理','text/plain');return
                value=payload.get('password')
                if not isinstance(value,str) or len(value.encode())>1024:
                    raise ValueError('密码无效或超过 1024 字节')
                if not value and not self.local_request():
                    self.send(403,'请在局域网内关闭访问密码','text/plain');return
                with db() as c:
                    c.execute('begin immediate')
                    if not self.auth(c):return
                    if value:
                        salt=secrets.token_bytes(16)
                        verifier=hashlib.pbkdf2_hmac('sha256',value.encode(),salt,600000)
                        c.execute('insert or replace into admin_access values(1,?,?)',(salt,verifier))
                    else:
                        c.execute('delete from admin_access')
            elif self.path=='/api/job': start(payload['action'],payload.get('url'),payload.get('urls'))
            elif self.path=='/api/stop-probe': stop_probe(payload.get('run_id'),payload.get('url'))
            elif self.path in ('/api/clear-logs','/api/settings','/api/source','/api/source-state','/api/delete','/api/channel','/api/channel-logo','/api/channels','/api/route','/api/routes','/api/hide-routes','/api/restore-routes','/api/hide-channels','/api/visibility'):
                edit(self.path.split('/')[-1],payload)
            else:
                self.send(404,'未知操作','text/plain');return
            self.send(200,'{"ok":true}','application/json')
        except (ValueError,KeyError,TypeError) as exc: self.send(400,str(exc),'text/plain')

if __name__=='__main__':
    init()
    if not PASSWORD and not LAN_NO_PASSWORD and HOST not in ('localhost','127.0.0.1','::1'):
        key=DATA/'admin-password'
        if not key.exists(): key.write_text(secrets.token_urlsafe(20),encoding='utf-8')
        PASSWORD=key.read_text(encoding='utf-8').strip()
        if not PASSWORD: raise ValueError('管理员密码为空')
        print('管理员 admin 的密码保存在 '+str(key),flush=True)
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    if os.getenv('STUDIO_PREVIEW')!='1':
        threading.Thread(target=scheduler,daemon=True).start()
    print(f'IPTV Hub · http://{HOST}:{PORT}',flush=True)
    server.serve_forever()

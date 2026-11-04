import os, sys, time, json, gzip, threading, sqlite3, subprocess, tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from xml.etree import ElementTree as ET
from collections import Counter
from contextlib import contextmanager
import socket

project=Path(__file__).resolve().parent
hits=Counter()
fail=False
# A 1280 × 720 AVC SPS in the video PES of a small TS fixture.
def ue(n):
    bits=bin(n+1)[2:]
    return '0'*(len(bits)-1)+bits
bits=''.join(ue(n) for n in (0,0,0,0,1))+'0'+ue(79)+ue(44)+'11001'
bits+='0'*(-len(bits)%8)
sps=b'\x00\x00\x01\x67\x42\x00\x1e'+int(bits,2).to_bytes(len(bits)//8,'big')
packet=b'\x47\x41\x01\x10\x00\x00\x01\xe0\x00\x00\x80\x00\x00'+sps
sample=packet.ljust(188,b'\xff')*32
class Fixture(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        path=self.path
        hits[path]+=1
        if path=='/a': out='#EXTM3U\n#EXTINF:-1 tvg-id="x",CCTV-1\n/master\n#EXTINF:-1,成都生活\n/fail\n'
        elif path=='/b': out='地方,#genre#\nCCTV1综合,'+origin+'/master#'+origin+'/variant\n'
        elif path=='/epg':
            out=gzip.compress(b'<tv><channel id="x"><display-name>CCTV-1</display-name><icon src="icons/one.svg"/></channel><programme start="20261101010000 +0800" channel="x"><title>Sample</title></programme></tv>')
        elif path=='/icons/one.svg': out='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20" fill="green"/></svg>'
        elif path=='/master': out='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4000000\nmedia\n'
        elif path in ('/media','/variant'): out='#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nfirst.ts\n#EXTINF:4,\nlast.ts\n'
        elif path=='/last.ts': out=sample; time.sleep(.025)
        else: out='failure'
        code=503 if path=='/a' and fail else 401 if path=='/fail' else 200
        out=out.encode() if isinstance(out,str) else out
        self.send_response(code)
        self.send_header('Content-Length',str(len(out)))
        if path=='/last.ts': self.send_header('Content-Type','video/mp2t')
        if path=='/icons/one.svg': self.send_header('Content-Type','image/svg+xml')
        try:
            self.end_headers()
            self.wfile.write(out)
        except OSError: pass
mock=ThreadingHTTPServer(('127.0.0.1',0),Fixture);origin='http://127.0.0.1:'+str(mock.server_port)
threading.Thread(target=mock.serve_forever,daemon=True).start()
sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
base='http://127.0.0.1:'+str(port)
def request(p,data=None):
    req=Request(base+p,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=10) as r: return r.read()
def api(p,data=None): return json.loads(request('/api/'+p,data))
def waitjob():
    for _ in range(350):
        if not api('status')['job']['running']: return
        time.sleep(.12)
    assert False,'job timeout'
@contextmanager
def app(folder):
    p=subprocess.Popen([sys.executable,'app.py'],cwd=project,env={**os.environ,'DATA_DIR':folder,'PORT':str(port),'BIND':'127.0.0.1','ADMIN_PASSWORD':'','PYTHONDONTWRITEBYTECODE':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for i in range(50):
            try: request('/health');break
            except OSError: time.sleep(.15)
        assert p.poll() is None
        yield
    finally: p.terminate();p.wait(timeout=5)

with tempfile.TemporaryDirectory() as temp:
    folder=str(Path(temp).resolve())
    with app(folder):
        st=api('state');assert (st['exported'],st['lines'])==(0,0) and not st['sources']
        for name,path,kind,priority in [('A','a','playlist',1),('B','b','playlist',2),('节目','epg','epg',1)]:
            api('source',dict(name=name,url=origin+'/'+path,kind=kind,enabled=True,priority=priority))
        api('job',dict(action='refresh'));waitjob()
        st=api('state');assert len(st['channels'])==2 and st['exported']==0
        assert all(not r['enabled'] for c in st['channels'] for r in c['lines'])
        ch=next(c for c in st['channels'] if c['name']=='CCTV1综合')
        assert len(ch['lines'])==2 and ch['lines'][0]['from']==['A','B'] and ch['epg']=='x'
        assert ch['logo']==origin+'/icons/one.svg'
        cfg=dict(probe_mode='legacy',minutes=1,mode='merge',strict=False,auto_probe=False)
        api('settings',cfg)
        def visibility(kind,name,hide=True,url=''):
            api('visibility',dict(kind=kind,name=name,hide=hide,url=url))
        channel_names=['CCTV1综合','成都生活']
        try:api('hide-channels',dict(names=[channel_names[0],'不存在']));assert False
        except HTTPError as e:assert e.code==400
        assert not api('state')['hidden']
        for _ in range(2):api('hide-channels',dict(names=channel_names))
        assert not api('state')['channels'] and not json.loads(request('/live.json'))
        channel=channel_names[0]
        def remaining_channel():
            return {r['url'] for r in api('state')['hidden_channels'].get(channel,[])}
        master=origin+'/master'
        variant=origin+'/variant'
        assert remaining_channel()=={master,variant}
        def batch_restore(scope,owner,targets):
            api('restore-routes',dict(scope=scope,owner=owner,targets=[dict(name=n,url=u) for n,u in targets]))
        def check_batch(scope,owner):
            try:batch_restore(scope,owner,[(channel,master),('不存在',variant)]);assert False
            except HTTPError as e:assert e.code==400
            assert master.encode() not in request('/live.m3u')
            for _ in range(2):batch_restore(scope,owner,[(channel,master),(channel,variant),(channel,master)])
            body=request('/live.m3u')
            assert master.encode() in body and variant.encode() in body
            visibility(scope,owner)
        check_batch('channel',channel)
        restore_one=dict(kind='route',name=channel,url=master,channel=channel,hide=False)
        for _ in range(2):api('visibility',restore_one)
        assert remaining_channel()=={variant}
        for path in ('/live.m3u','/live.txt','/live.json'):
            data=request(path)
            assert master.encode() in data and variant.encode() not in data
        visibility('route',channel,url=master)
        assert not api('state')['channels'] and remaining_channel()=={variant}
        visibility('route',channel,False,master)
        api('job',dict(action='refresh'));waitjob()
        assert remaining_channel()=={variant} and master.encode() in request('/live.m3u')
        api('hide-channels',dict(names=[channel]))
        assert remaining_channel()=={master,variant} and not api('state')['channels']
        try:api('visibility',{**restore_one,'url':origin+'/absent'});assert False
        except HTTPError as e:assert e.code==400
        visibility('channel',channel_names[0],False)
        assert [c['name'] for c in api('state')['channels']]==channel_names[:1]
        visibility('channel',channel_names[1],False)
        pair=[dict(name='CCTV1综合',url=origin+'/master'),dict(name='成都生活',url=origin+'/fail')]
        try:api('hide-routes',dict(targets=[pair[0],dict(name='无此线路',url=origin+'/fail')]));assert False
        except HTTPError as e:assert e.code==400
        assert not api('state')['hidden']
        api('hide-routes',dict(targets=pair))
        api('hide-routes',dict(targets=pair))
        assert len(api('state')['hidden'])==2
        for path in ('/live.m3u','/live.txt','/live.json'):
            result=request(path)
            assert (origin+'/variant').encode() in result and all(t['url'].encode() not in result for t in pair)
        visibility('channel',channel)
        try:api('restore-routes',dict(scope='route',targets=[pair[0],{}]));assert False
        except HTTPError as e:assert e.code==400
        assert len(api('state')['hidden'])==3
        for _ in range(2):api('restore-routes',dict(scope='route',targets=pair))
        assert api('state')['hidden']==[dict(kind='channel',name=channel,url='')]
        assert master.encode() not in request('/live.m3u')
        visibility('channel',channel,False)
        visibility('route','CCTV1综合',url=origin+'/master')
        for path in ('/live.m3u','/live.txt','/live.json'):
            body=request(path)
            assert (origin+'/master').encode() not in body and (origin+'/variant').encode() in body
        visibility('channel','CCTV1综合')
        api('job',dict(action='refresh'));waitjob()
        assert not any(c['group']=='央视' for c in api('state')['channels'])
        assert ET.fromstring(request('/guide.xml')).find("channel[@id='x']") is None
        visibility('channel','CCTV1综合',False)
        assert (origin+'/master').encode() not in request('/live.m3u')
        visibility('group','央视')
        visibility('route','CCTV1综合',False,origin+'/master')
        for path in ('/live.m3u','/live.txt','/live.json'):
            assert b'CCTV1' not in request(path) and (origin+'/fail').encode() in request(path)
        assert ET.fromstring(request('/guide.xml')).find('programme') is None
        def restore_line(group,name,path):
            api('visibility',dict(kind='route',name=name,url=origin+path,group=group,hide=False))
        assert {r['url'] for r in api('state')['hidden_groups']['央视']}=={origin+'/master',origin+'/variant'}
        check_batch('group','央视')
        try: restore_line('央视','成都生活','/fail');assert False
        except HTTPError as e:assert e.code==400
        restore_line('央视','CCTV1综合','/master')
        restore_line('央视','CCTV1综合','/master')
        def group_urls(group):
            return {r['url'] for r in api('state')['hidden_groups'].get(group,[])}
        assert group_urls('央视')=={origin+'/variant'}
        visibility('route','CCTV1综合',url=origin+'/master')
        assert (origin+'/master').encode() not in request('/live.m3u') and group_urls('央视')=={origin+'/variant'}
        visibility('route','CCTV1综合',False,origin+'/master')
        assert (origin+'/master').encode() in request('/live.m3u') and group_urls('央视')=={origin+'/variant'}
        api('job',dict(action='refresh'));waitjob()
        assert group_urls('央视')=={origin+'/variant'}
        for path in ('/live.m3u','/live.txt','/live.json'):
            body=request(path)
            assert (origin+'/master').encode() in body and (origin+'/variant').encode() not in body
        assert ET.fromstring(request('/guide.xml')).find("channel[@id='x']") is not None
        api('channel',dict(name='成都生活',group='央视'))
        assert (origin+'/fail').encode() not in request('/live.m3u')
        api('channel',dict(name='成都生活',group=''))
        restore_line('央视','CCTV1综合','/variant')
        assert not group_urls('央视')
        visibility('group','央视')  # A restored group can be hidden again.
        assert b'CCTV1' not in request('/live.m3u')
        visibility('group','央视',False)
        assert not api('state')['hidden'] and (origin+'/master').encode() in request('/live.m3u')
        assert len(json.loads(request('/live.json')))==2
        assert f'tvg-logo="{origin}/icons/one.svg"'.encode() in request('/live.m3u')
        assert ET.fromstring(request('/guide.xml')).find("channel[@id='x']/icon").get('src')==ch['logo']
        api('channel',dict(name='CCTV1综合',logo=origin+'/override.png'))
        overridden=api('state')['channels'][0]
        assert overridden['logo_candidates']==[origin+'/override.png'] and overridden['auto_logo_candidates'][0]==origin+'/icons/one.svg'
        api('channel',dict(name='CCTV1综合',logo=''))
        assert api('state')['channels'][0]['logo']==origin+'/icons/one.svg'
        assert '成都生活'.encode() in request('/live.txt')
        api('channel',dict(name='成都生活',target='CCTV1综合'))
        assert len(api('state')['channels'])==1
        api('channel',dict(name='成都生活',target=''))
        assert len(api('state')['channels'])==2
        assert ET.fromstring(request('/guide.xml')).find('programme').get('channel')=='x'
        api('job',dict(action='probe',url=origin+'/master'))
        try: api('job',dict(action='refresh'));assert False
        except HTTPError as e: assert e.code==400
        waitjob();assert not hits['/first.ts'] and hits['/last.ts']>1
        ch=next(c for c in api('state')['channels'] if c['name']=='CCTV1综合')
        t=ch['lines'][0]['test'];assert t['seconds']==8 and t['speed']==t['bytes']/8000 and t['status']=='保留'
        assert t['resolution']=={'width':1280,'height':720,'source':'末片视频头'}
        def line_state(path):
            return next(r for c in api('state')['channels'] for r in c['lines'] if r['url']==origin+path)
        for criterion,height,threshold,num in [('resolution',720,999999,1),('resolution',1080,0,0),('both',720,999999,0),('speed',1080,0,1)]:
            api('settings',{**cfg,'strict':True,'criteria':criterion,'min_height':height,'min_speed':threshold})
            assert len(json.loads(request('/live.json')))==num
            assert line_state('/master')['enabled'] is bool(num) and not line_state('/fail')['enabled']
        api('settings',{**cfg,'criteria':'speed','min_speed':100})
        targets=[dict(name='CCTV1综合',url=origin+'/variant'),dict(name='成都生活',url=origin+'/fail')]
        api('routes',dict(targets=targets,mode='off'))
        assert len(json.loads(request('/live.json')))==1 and len(json.loads(request('/live.json'))[0]['lines'])==1
        try: api('routes',dict(targets=[targets[0],dict(name='不存在',url=origin+'/fail')],mode='on'));assert False
        except HTTPError as e: assert e.code==400
        assert len(json.loads(request('/live.json'))[0]['lines'])==1
        api('routes',dict(targets=targets,mode='auto'))
        assert request('/live.m3u')==request('/live.m3u?view=text')==request('/live.m3u?view=download')
        raw=request('/live.m3u')
        for accept,typ in [('*/*','text/plain'),('text/html,*/*','text/plain'),('application/vnd.apple.mpegurl','application/vnd.apple.mpegurl')]:
            with urlopen(Request(base+'/live.m3u',headers={'Accept':accept})) as r:
                assert r.headers.get_content_type()==typ and 'attachment' not in r.headers.get('Content-Disposition','')
                assert r.read()==raw and raw.startswith(b'#EXTM3U')
        from catalog import speed_display
        assert [speed_display(x) for x in (None,0,512,1024,1536)]==['—','0.00 KB/s','512.00 KB/s','1.00 MB/s','1.50 MB/s']
        with urlopen(base+'/live.m3u?view=download') as r: assert 'attachment' in r.headers['Content-Disposition']
        with urlopen(base+'/live.m3u?view=text') as r: assert r.headers.get_content_type()=='text/plain'
        api('job',dict(action='probe',url=origin+'/fail'));waitjob()
        assert any('401' in str(c['lines'][0]['test']) for c in api('state')['channels'])
        for invalid in ([],[origin+'/master',origin+'/absent']):
            try: api('job',dict(action='probe',urls=invalid));assert False
            except HTTPError as e: assert e.code==400
        before=(hits['/master'],hits['/fail'],hits['/variant'])
        revision=api('status')['job']['revision']
        api('job',dict(action='probe',urls=[origin+'/fail',origin+'/master',origin+'/master']))
        for _ in range(80):
            live=api('status')['job']
            if live['active_url']==origin+'/master' and live['sample']: break
            time.sleep(.1)
        assert live['running'] and live['sample'] and live['revision']==revision+1
        assert any('401' in str(c['lines'][0]['test']) for c in api('state')['channels'])
        waitjob()
        assert (hits['/master'],hits['/fail'],hits['/variant'])==(before[0]+1,before[1]+1,before[2])
        assert api('status')['job']['total']==2
        logs=api('logs?kind=probe')
        assert logs[0]['source']=='A / B' and logs[0]['result']=='通过'
        assert any('401' in row.get('reason','') and row['source']=='A' for row in logs)
        assert {r['source'] for r in api('logs?kind=refresh') if r['result']=='成功'}=={'A','B','节目'}
        # Stop an active subprocess, once skipping this URL and once ending the queue.
        for whole in (False,True):
            previous=line_state('/master')['test']
            hits_before=hits['/fail']
            api('job',dict(action='probe',urls=[origin+'/master',origin+'/fail']))
            for _ in range(65):
                current=api('status')['job']
                if current.get('sample'): break
                time.sleep(.1)
            assert current['active_url']==origin+'/master' and current['active_channel']=='CCTV1综合'
            payload={'run_id':current['run_id']}
            if not whole: payload['url']=current['active_url']
            api('stop-probe',payload)
            waitjob()
            assert line_state('/master')['test']==previous and hits['/fail']==hits_before+int(not whole)
            latest=next(r for r in api('logs?kind=probe') if r['url']==origin+'/master')
            assert latest['result']==('已终止' if whole else '已跳过')
            assert latest['result'] in api('status')['job']['message']
            try: api('stop-probe',payload);assert False
            except HTTPError as e: assert e.code==400

        api('channel',dict(name='CCTV1综合',group='自选',label='一台',logo='http://example.test/logo'))
        api('settings',{**cfg,'strict':True,'mode':'best'})
        assert len(json.loads(request('/live.json')))==1 and '自选'.encode() in request('/live.m3u')
        def route(name,url,mode): api('route',dict(name=name,url=origin+url,mode=mode))
        both=['CCTV1综合','成都生活']
        route(both[0],'/master','off')
        assert not json.loads(request('/live.json'))
        route(both[1],'/fail','on')
        api('job',dict(action='probe',url=origin+'/fail'));waitjob()
        assert not line_state('/fail')['enabled'] and line_state('/fail')['mode']=='auto'
        assert not line_state('/master')['enabled'] and line_state('/master')['mode']=='off'
        api('job',dict(action='probe',url=origin+'/master'));waitjob()
        assert line_state('/master')['enabled'] and line_state('/master')['mode']=='auto'
        for path in ('/live.m3u','/live.txt','/live.json'):
            result=request(path)
            assert (origin+'/master').encode() in result and (origin+'/fail').encode() not in result
        route(both[0],'/master','off')
        route(both[1],'/fail','on')
        for path in ('/live.m3u','/live.txt','/live.json'):
            result=request(path)
            assert (origin+'/fail').encode() in result and (origin+'/master').encode() not in result
        api('channels',dict(names=both,enabled=False))
        assert not ET.fromstring(request('/guide.xml')).findall('channel')
        assert not line_state('/fail')['enabled'] and not line_state('/fail')['published']
        assert not line_state('/master')['enabled']
        try: api('channels',dict(names=[both[0],'无此频道'],enabled=True));assert False
        except HTTPError as e: assert e.code==400
        assert not json.loads(request('/live.json'))
        api('channels',dict(names=both,enabled=True))
        assert line_state('/fail')['enabled'] and line_state('/fail')['published']
        assert line_state('/master')['enabled'] and line_state('/master')['published']
        route(both[1],'/fail','auto')
        route(both[0],'/master','auto')
        route(both[0],'/variant','off')
        fail=True;api('job',dict(action='refresh'));waitjob()
        assert any(r['source']=='A' and '503' in r.get('reason','') for r in api('logs?kind=refresh'))
        assert len(json.loads(request('/live.json')))==1 and '失败' in api('state')['sources'][0]['last']
        sources={r['name']:r for r in api('state')['sources']}
        api('source',{**sources['A'],'interval_hours':0.5})
        api('source',{**sources['B'],'interval_hours':0})
        def schedules():return {r['name']:r for r in api('state')['sources']}
        plans=schedules()
        assert plans['A']['next_update']-int(time.time()) in range(1798,1802) and plans['B']['next_update']==0
        api('settings',{**cfg,'hours':2,'strict':True,'mode':'best'})
        plans2=schedules()
        assert plans2['A']['next_update']==plans['A']['next_update'] and plans2['节目']['next_update']-int(time.time()) in range(7198,7202)
        api('source',{**plans2['节目'],'enabled':False})
        assert schedules()['节目']['next_update']==0
        api('source',{**plans2['节目'],'enabled':True})
        try:api('source',{**sources['A'],'interval_hours':-1});assert False
        except HTTPError as e:assert e.code==400
        before=hits.copy()
        with sqlite3.connect(Path(folder)/'hub.sqlite') as conn:
            conn.execute("update sources set next_update=? where name='A'",(int(time.time())-10,))
        conn.close()
        for i in range(100):
            if hits['/a']>before['/a']:break
            time.sleep(.1)
        waitjob()
        assert hits['/a']==before['/a']+1 and all(hits[p]==before[p] for p in ('/b','/epg'))
        snapshot=schedules()
        assert '失败' in snapshot['A']['last'] and snapshot['A']['next_update']>time.time()
        api('job',dict(action='refresh'));waitjob()
        assert all(hits[p]==before[p]+1 for p in ('/b','/epg'))
        visibility('group','自选')
        restore_line('自选','CCTV1综合','/variant')
    with app(folder):
        assert api('state')['exported']==0 and {'kind':'group','name':'自选','url':''} in api('state')['hidden']
        assert schedules()['A']['next_update']==snapshot['A']['next_update'] and schedules()['B']['interval_hours']==0
        remaining=api('state')['hidden_groups']['自选']
        assert {r['url'] for r in remaining}=={origin+'/master'}
        restored=next(c for c in api('state')['channels'] if c['name']=='CCTV1综合')
        assert [r['url'] for r in restored['lines']]==[origin+'/variant']
        assert origin+'/variant' not in request('/live.m3u').decode()
        visibility('group','自选',False)
        st=api('state');assert st['exported']==1 and st['channels'][0 if st['channels'][0]['name']=='CCTV1综合' else 1]['label']=='一台'
        ch=next(c for c in st['channels'] if c['name']=='CCTV1综合')
        assert next(r for r in ch['lines'] if r['url']==origin+'/variant')['mode']=='off'
        assert b'<!doctype html>' in request('/')
mock.shutdown()
from catalog import normalize,build,export
from catalog import logo_keys,parse_playlist
fixture_url='https://fixture.local/sub/list.m3u'
items=parse_playlist('#EXTM3U\n#EXTINF:-1 tvg-name="CCTV5" tvg-logo="../five.png",体育主线\n/one\n#EXTINF:-1,CCTV5体育\n/two\n#EXTINF:-1,CCTV5+体育赛事\n/three\n',fixture_url)
fixture=[dict(id=1,name='订阅',priority=1,items=items)]
matched={c['name']:c for c in build(fixture,{},{},epg_logos={logo_keys(None,'CCTV-5+')[0]:'https://fixture.local/plus.png'})}
assert matched['CCTV5体育']['logo']=='https://fixture.local/five.png'
assert matched['CCTV5+体育赛事']['logo']=='https://fixture.local/plus.png'
# A faster lower-priority subscription should win in speed mode; height wins in resolution mode.
from catalog import selected
name='CCTV1综合'
urls=[fixture_url+str(i) for i in range(4)]
samples={urls[0]:dict(seconds=8,speed=300,resolution=dict(width=1920,height=1080)),
         urls[1]:dict(seconds=8,speed=1200,resolution=dict(width=1280,height=720)),
         urls[3]:dict(status='清单失败')}
feeds=[dict(id=i,name=str(i),priority=i,items=[dict(name=name,url=u,attrs={},options=[])]) for i,u in enumerate(urls)]
for criteria,first in [('speed',1),('resolution',0),('both',0)]:
    settings=dict(criteria=criteria,strict=True,mode='best')
    channels=build(feeds,samples,{},settings=settings)
    order=[urls[first],urls[1-first],urls[2],urls[3]]
    assert [r['url'] for r in channels[0]['lines']]==order
    assert next(selected(channels,settings))['lines'][0]['url']==order[0]
    for kind,owner in [('group','央视'),('channel',name),('route',name)]:
        inventory={}
        explicit=[dict(kind=kind,name=owner,url=u) for u in urls] if kind=='route' else []
        hidden={(kind,owner,r['url']) for r in explicit} or {(kind,owner,'')}
        build(feeds,samples,{},settings=settings,hidden=hidden,hidden_items=explicit,
              hidden_groups=inventory if kind=='group' else None,hidden_channels=inventory if kind=='channel' else None)
        entries=explicit or inventory[owner]
        assert [r['url'] for r in entries]==order
        assert entries[0]['test']['resolution']==samples[order[0]]['resolution']
        assert entries[0]['test']['speed']==samples[order[0]]['speed']
        assert entries[-2]['speed_display']=='—' and entries[-2]['assessment']=='未测'
from grouping import classify,programme_profiles
for raw,hint,name,group in [
    ('📺【高清】CCTV-1','☘️广东频道','CCTV1综合','央视'),('【高清】重庆卫视','📺央视频道','重庆卫视','卫视'),
    ('广东珠江','☘️广东频道','广东珠江','地方综合'),('风云音乐','💰央视付费频道','风云音乐','央视'),
    ('经典电影','🎬电影频道','经典电影','影视文娱'),('未知频道','🌟自由命名','未知频道','其他'),
    ('广东体育','☘️广东频道','广东体育','体育电竞'),('上海第一财经','上海频道','上海第一财经','新闻财经'),
    ('山东少儿','山东频道','山东少儿','少儿科教'),('之江纪录','浙江频道','之江纪录','纪实生活'),
    ('凤凰资讯','港澳台','凤凰资讯','港澳台'),('CGTN纪录','央视','CGTN纪录','央视'),
    ('老故事','央视','老故事','央视'),('中国农林卫视','农业农村','中国农林卫视','卫视'),('CCTV5+','体育','CCTV5+体育赛事','央视'),('凤凰卫视','港澳台','凤凰卫视','港澳台'),('CETV-1','📺央视频道','CETV-1','少儿科教')]:
    assert normalize(raw)==name and classify(name,{1:{hint}},{},[name])[0]==group
for n,label,country,expected in [
    ('鳳凰衛視','新闻','', '港澳台'),('緯來體育','体育','', '港澳台'),('翡翠台','海外','', '港澳台'),
    ('NHK WORLD','新闻','', '国外'),('BBCNEWS','港澳台','', '国外'),('CCTV4欧洲','国外','GB','央视'),
    ('CGTN法语','国外','FR','央视'),('北京卫视','海外','US','卫视'),('未知新闻','🌊港·澳·台','','港澳台'),
    ('国际新闻','国际','','新闻财经'),('体育台','体育','US','国外'),('体育台','海外','CN-HK','港澳台'),
    ('体育台','国外','CN','体育电竞'),('未知台','🇯🇵','','国外'),('未知台','美国频道','','国外')]:
    assert classify(n,{1:{label}},{},[n],{1:{country}})[0]==expected,(n,expected)
assert classify('未知新闻',{1:{'港澳台'},2:{'国外'}},{},[])[0]=='新闻财经'
regional_source=[dict(id=1,name='甲',priority=1,items=[dict(name='测试体育',url='https://example.test/1',attrs={'tvg-country':'TW'},options=[])])]
regional=build(regional_source,{}, {})
assert regional[0]['group']=='港澳台' and 'group-title="港澳台"' in export('m3u',regional,'/guide.xml')
assert build(regional_source,{}, {'测试体育':{'group':'自定义'}})[0]['group']=='自定义'
schedule=ET.Element('tv')
key=ET.SubElement(schedule,'channel',{'id':'station-24'})
ET.SubElement(key,'display-name').text='未知频道'
for i in range(7):
    item=ET.SubElement(schedule,'programme',{'channel':'station-24','start':str(i)})
    ET.SubElement(item,'title').text=f'篮球联赛第{i}场' if i<5 else f'综合节目{i}'
profiles=programme_profiles(schedule)
assert classify('未知频道',{1:{'🎬电影频道'}},profiles,['未知频道'])[0]=='体育电竞'
for station,expected in [('CCTV5体育','央视'),('CGTN纪录','央视'),('中国农林卫视','卫视')]:
    assert classify(station,{1:{'体育频道'}},profiles,['station-24'])[0]==expected
assert classify('未知频道',{},profiles,['CCTV8','station-24'])[0]=='央视'
assert classify('未知频道',{1:{'音乐频道'},2:{'电影频道'}},{},[])[0]=='影视文娱'
ET.SubElement(ET.SubElement(schedule,'programme',{'channel':'station-24','start':'8'}),'title').text='综合节目'
assert not programme_profiles(schedule)
assert classify('未知频道',{1:{'电影频道'},2:{'体育频道'}},{},[])[0]=='其他'
rows=[{'name':'未知频道','url':'http://example.test/a','attrs':{'group-title':'☘️广东频道','tvg-id':'station-24'},'options':[]}]
sources=[dict(id=1,name='甲',priority=1,items=rows)]
ch=build(sources,{}, {'未知频道':{'group':'自选'}},profiles=profiles)
assert ch[0]['group']=='自选' and ch[0]['auto_group']=='体育电竞' and 'group-title="自选"' in export('m3u',ch,'/guide.xml')
assert len({normalize(n) for n in ['CCTV4K','CCTV4中文国际','CCTV4欧洲','CCTV5','CCTV5+']})==5
print('PASS: empty first start, source CRUD, M3U/TXT import, dedup, priority, aliases, EPG gzip/ID, scheduler, 8s throughput, rejection, strict exports, stale snapshot and restart')


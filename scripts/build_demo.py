"""Build a Pages demo from synthetic data only; never read a user database."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory() as data:
    os.environ['DATA_DIR']=data
    import service
    service.init()
    with service.db() as c:
        items=[]
        for i,name in enumerate(['晨光综合','城市新闻','山海纪录','星河电影','绿茵体育','童趣动画','旅行生活','经典音乐']):
            for n in range(2):
                url=f'https://stream.example.invalid/channel-{i}/{n}.m3u8'
                items.append(dict(name=name,url=url,attrs={},options=[]))
                result=dict(method='continuous',quality='流畅',advanced=True,speed=380+i*85+n*25,resolution=dict(width=1920,height=1080),seconds=24,play_seconds=24,startup=1.2,stalls=0,opening='直连可播',note=['演示模拟结果，未连接直播源'],at='2026-11-04T00:00:00Z')
                c.execute('insert into tests values(?,?)',(url,json.dumps(result)))
        c.execute('insert into sources(name,kind,url,enabled,priority,items) values(?,?,?,?,?,?)',('演示直播订阅','playlist','https://subscription.example.invalid/demo.m3u',1,1,json.dumps(items)))
        channels=service.catalog(c)
        settings=service.config(c)
    state=dict(preview=True,settings=settings,channels=channels,exported=len(channels),lines=len(items),sources=[dict(id=1,name='演示直播订阅',kind='playlist',url='https://subscription.example.invalid/demo.m3u',priority=1,enabled=True,last='模拟数据 · 不连接真实直播源',updated=0,interval_hours=0,next_update=0,guide_urls=[])],password_mode='off',hidden=[],hidden_groups={},hidden_channels={},rules={})

out=ROOT/'demo-dist'
out.mkdir(exist_ok=True)
for name in ['style.css','hls.min.js','hls-LICENSE.txt']:
    shutil.copyfile(ROOT/name,out/name)
html=(ROOT/'index.html').read_text()
html=html.replace('href="/','href="./').replace('src="/','src="./')
html=html.replace('<body>','<body><div style="padding:12px;text-align:center;background:#e5f0dc">公开演示 · 全部频道及检测结果均为模拟；刷新恢复。播放、真实测速及服务器设置不可用。</div>')
html=html.replace('<script src="./ui.js">','<script src="./demo.js"></script><script src="./ui.js">')
(out/'index.html').write_text(html)
ui=(ROOT/'ui.js').read_text().replace("const r=await fetch('/api/'+path", "if(window.demoAPI)return window.demoAPI(path,data);\n const r=await fetch('/api/'+path")
ui=ui.replace("state.preview?'预览模式'", "state.preview?'模拟演示'")
(out/'ui.js').write_text(ui)
(out/'demo-state.json').write_text(json.dumps(state,ensure_ascii=False))
shutil.copyfile(ROOT/'demo/demo.js',out/'demo.js')
from catalog import export
for kind in ['m3u','txt','json']:
    (out/f'live.{kind}').write_text(export(kind,channels,'./guide.xml'))
(out/'guide.xml').write_text('<?xml version="1.0" encoding="UTF-8"?><tv/>')
(out/'.nojekyll').touch()
print('Built synthetic demo:',out)

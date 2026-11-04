"""Transport-to-guide regression using generated PAT, SDT and DVB EIT sections."""
import tempfile
import threading
import time
import unittest
import json
import socket
import subprocess
import base64
from urllib.request import urlopen
from urllib.parse import quote
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET

import service
from app import Handler as StudioHandler
from catalog import export, selected
from eit import crc32, programmes


def word(n):return n.to_bytes(2,'big')


def section(tid,key,body):
    data=bytes([tid])+word(0xb000+len(body)+9)+word(key)+bytes([0xc1,0,0])+body
    return data+crc32(data).to_bytes(4,'big')


def packetize(pid,data,cc=0):
    chunks=[]
    data=b'\0'+data
    while data:
        h=bytes([0x47,(pid>>8)|(0 if chunks else 0x40),pid&255,16+cc%16])
        chunks.append(h+data[:184].ljust(184,b'\xff'))
        data=data[184:]
        cc+=1
    return b''.join(chunks)


def event(sid,title,day,tid=0x50):
    name=b'\x13'+title.encode('gb2312')
    desc=b'A'*170
    d=b'zho'+bytes([len(name)])+name+bytes([len(desc)])+desc
    descriptors=bytes([0x4d,len(d)])+d
    entry=word(456)+word(day)+bytes.fromhex('080000010000')+word(len(descriptors))+descriptors
    return section(tid,sid,word(1)+word(2)+bytes([0,tid])+entry)


class EitTest(unittest.TestCase):
    def test_rtp_transport(self):
        data=packetize(0,section(0,1,word(7)+word(0xe100)))+packetize(18,event(7,'新闻',int(time.time()//86400)+40588))
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as pick:
            pick.bind(('127.0.0.1',0))
            port=pick.getsockname()[1]
        with subprocess.Popen(['node',str(service.ROOT/'probe.mjs'),f'rtp://127.0.0.1:{port}','--eit'],stdout=subprocess.PIPE) as proc:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                for i in range(16):
                    time.sleep(.05)
                    sock.sendto(bytes([0x80,33])+word(i)+bytes(8)+data,('127.0.0.1',port))
            result=json.loads(proc.communicate(timeout=12)[0])
        rows=programmes(base64.b64decode(result['packets']),{'name':'测试频道','attrs':{}})
        self.assertEqual([r[2] for r in rows],['新闻'])

    def test_update_to_player(self):
        day=int(time.time()//86400)+40588
        pat=packetize(0,section(0,1,word(7)+word(0xe100)+word(8)+word(0xe200)))
        name=b'\x15'+ '测试频道'.encode()
        label=bytes([1,0,len(name)])+name
        descriptor=bytes([0x48,len(label)])+label
        sdt=packetize(17,section(0x42,1,word(2)+b'\xff'+word(7)+b'\xff'+word(len(descriptor))+descriptor))
        first=event(7,'新闻',day,0x4e)
        schedule=event(7,'明日',day+1)
        data=pat+sdt+packetize(18,first)+packetize(18,schedule,2)+packetize(18,event(8,'体育',day),4)
        item={'name':'测试频道','attrs':{}}
        self.assertEqual([r[2] for r in programmes(data,item)],['新闻','明日'])
        self.assertFalse(programmes(pat+packetize(18,first),item))
        self.assertFalse(programmes(pat+sdt+packetize(18,first[:-1]+bytes([first[-1]^1])),item))
        self.assertFalse(programmes(pat+sdt+packetize(18,event(7,'他台',day,0x4f)),item))
        self.assertEqual(programmes(data,{**item,'attrs':{'service-id':'8'}})[0][2],'体育')
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path=='/feed':
                    payload='#EXTM3U\n#EXTINF:-1 tvg-id="guide-7",测试频道\n/master\n#EXTINF:-1 service-id="8",第二台\n/raw'.encode()
                elif self.path=='/master':payload=b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlive'
                elif self.path=='/live':payload=b'#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nraw'
                else:payload=data
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers()
                self.wfile.write(payload)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        with tempfile.TemporaryDirectory() as folder,patch.object(service,'DATA',Path(folder)):
            service.init()
            with service.db() as c:
                c.execute('delete from sources')
                c.execute("insert into sources(name,kind,url,priority,enabled) values('DVB','playlist',?,1,1)",(base+'/feed',))
            service.run('refresh',None)
            with service.db() as c:
                service.save_config(c,{**service.config(c),'strict':False})
            api=ThreadingHTTPServer(('127.0.0.1',0),StudioHandler)
            threading.Thread(target=api.serve_forever,daemon=True).start()
            try:
                studio=f'http://127.0.0.1:{api.server_port}'
                with urlopen(studio+'/api/guide?channel='+quote('测试频道')) as response:
                    self.assertEqual([p['title'] for p in json.load(response)],['新闻','明日'])
                with urlopen(studio+'/live.m3u') as response:
                    self.assertIn(f'x-tvg-url="{studio}/guide.xml"',response.read().decode())
                with urlopen(studio+'/guide.xml') as response:
                    self.assertEqual(len(ET.parse(response).findall("programme[@channel='guide-7']")),2)
            finally:
                api.shutdown();api.server_close()
            with service.db() as c:
                self.assertEqual(c.execute('select count(*) from eit_events').fetchone()[0],3)
                ch=service.catalog(c)
                firstch=next(row for row in ch if row['epg']=='guide-7')
                detail=ET.fromstring(service.epg(c,[firstch]))
                self.assertEqual([p.findtext('title') for p in detail.findall('programme')],['新闻','明日'])
                active=list(selected(ch,{'strict':False,'mode':'best'}))
                self.assertIn('tvg-id="guide-7"',export('m3u',active,base+'/guide.xml'))
                guide=ET.fromstring(service.epg(c,active))
                self.assertEqual(len(guide.findall("programme[@channel='guide-7']")),2)
                # Same instant, different timezone: separately configured XMLTV takes priority.
                ts=detail.find('programme').get('start')[:8]+'160000 +0800'
                xml=f'<tv><programme start="{ts}" channel="guide-7"><title>XML</title></programme></tv>'
                c.execute("insert into sources(name,kind,url,enabled,priority) values('Guide','epg','test',1,1)")
                c.execute("insert into epg_cache values('test',?,'{}','{}',1)",(xml.encode(),))
                self.assertEqual({p.findtext('title') for p in ET.fromstring(service.epg(c,[firstch])).findall('programme')},{'XML','明日'})
                c.execute("update sources set enabled=0 where kind='playlist'")
                self.assertNotIn('明日',service.epg(c,[firstch]).decode())


if __name__=='__main__':unittest.main()

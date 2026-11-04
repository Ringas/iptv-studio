"""Verify playlist guide discovery, shared snapshots and subscription lifecycle."""
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import service
from catalog import playlist_guides


class EmbeddedGuideTest(unittest.TestCase):
    def test_refresh_merge_and_cleanup(self):
        base='https://fixture.invalid/list/main.m3u'
        a='https://fixture.invalid/one.xml.gz'
        b='https://fixture.invalid/list/two.xml'
        header=f'''#EXTM3U URL-TVG="../one.xml.gz, two.xml" x-tvg-url='{a}' '''
        self.assertEqual(playlist_guides('\ufeff'+header,base),[a,b])
        docs={base:header+'\n#EXTINF:-1 tvg-id="output" tvg-name="Station",One\n/live',
              a:'<tv><channel id="a"><display-name>Station</display-name></channel><programme channel="a" start="1"><title>Morning</title></programme></tv>',
              b:'<tv><programme channel="output" start="2"><title>Evening</title></programme></tv>'}
        hits=Counter()
        def fetch(url,limit):
            hits[url]+=1
            return docs[url].encode(),url
        with tempfile.TemporaryDirectory() as folder, patch.object(service,'DATA',Path(folder)), patch.object(service,'fetch',fetch), patch.object(service,'read_eit_routes',return_value=('',[])):
            service.init()
            with service.db() as c:
                c.execute('delete from sources')
                c.executemany('insert into sources(id,name,kind,url,priority,enabled) values(?,?,?,?,1,1)',
                              [(1,'TV','playlist',base),(2,'Manual','epg',a)])
            def run():
                service.run('refresh',None)
                with service.db() as c:
                    return [p.findtext('title') for p in ET.fromstring(service.epg(c,service.catalog(c))).findall('programme')]
            self.assertCountEqual(run(),['Morning','Evening'])
            self.assertEqual(hits[a],1)
            docs[b]='not XML'
            self.assertCountEqual(run(),['Morning','Evening'])
            with service.db() as c:
                self.assertIn('1/2',c.execute('select last from sources where id=1').fetchone()[0])
                c.execute('update sources set enabled=0 where id=2')
            self.assertCountEqual(run(),['Morning','Evening'])
            docs[base]=docs[base].replace(header,'#EXTM3U')
            self.assertEqual(run(),[])
            with service.db() as c:
                self.assertIsNone(c.execute('select 1 from epg_cache where url=?',(b,)).fetchone())
                c.execute('update sources set enabled=1 where id=2')
                self.assertEqual(len(ET.fromstring(service.epg(c,service.catalog(c))).findall('programme')),1)


if __name__=='__main__':
    unittest.main()

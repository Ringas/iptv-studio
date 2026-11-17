"""Offline pipeline admission boundaries."""
from unittest.mock import patch
import service
from verify_probe_schedule import exercise,GOOD
original=service.config
with patch.object(service,'config',side_effect=lambda c:{**original(c),'probe_mode':'pipeline'}):
 cases=[({**GOOD,'advanced':False,'coarse_rejected':True,'resolution':{'height':480}},['pipeline']),({**GOOD,'speed':1,'resolution':{'height':720}},['pipeline']),({**GOOD,'advanced':False,'candidate_possible':True,'resolution':{'height':480}},['pipeline'])]
 for coarse,expected in cases:
  calls,_,_,_=exercise(['https://gate.test/live'],lambda u,m,c:coarse if m=='pipeline' else {**GOOD,'resolution':{'height':720}})
  assert [m for _,m,_ in calls]==expected,calls
print('PASS: low decoded cutoff, low speed admitted, unresolved candidate admitted')

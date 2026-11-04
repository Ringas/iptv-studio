"""Offline two-stage scheduling and qualification regression."""
from unittest.mock import patch
import service
from verify_probe_schedule import exercise, GOOD

original=service.config
def config(c):
    return {**original(c),'probe_mode':'pipeline'}

with patch.object(service,'config',side_effect=config):
    good={**GOOD,'resolution':{'height':720},'advanced':True}
    calls,tests,_,_=exercise(['https://a.test/a','https://b.test/b'],lambda *_:good.copy())
    assert [m for _,m,_ in calls]==['quick','quick','continuous','continuous'],calls
    assert all(t['qualification_history']==[1] for t in tests.values()),tests
    assert all(t['stable_qualified'] for t in tests.values()),tests
    calls,tests,_,_=exercise(['https://a.test/a'],lambda *_:{'quality':'未能验证','httpStatus':403,'note':['denied']})
    assert len(calls)==2 and calls[1][2]-calls[0][2]>=8,calls
    assert tests['https://a.test/a']['qualification_history']==[0],tests
    assert not tests['https://a.test/a']['grace'],tests
    calls,tests,_,_=exercise(['https://a.test/a'],lambda *_:{**good,'speed':100})
    assert not tests['https://a.test/a']['stable_qualified'],tests
print('PASS: coarse before fine, decode evidence, strict threshold, cooled unknown retry, one history entry per round')

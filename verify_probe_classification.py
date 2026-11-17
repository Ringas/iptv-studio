"""Offline regression: inconclusive attempts cannot disable or qualify a route."""
from catalog import classify_result, assess
from verify_probe_schedule import exercise, GOOD

url='https://fixture.test/live'
cases=[({'httpStatus':429,'note':['limited']},'rate_limited'),
       ({'httpStatus':607,'note':['blocked']},'network_restricted'),
       ({'httpStatus':403,'note':['Blocked by network policy']},'network_restricted'),
       ({'quality':'暂时失败','retryable':True,'note':['入口超时']},'timeout')]
for attempt,category in cases:
    result=classify_result(attempt)
    assert result['error_category']==category
    assert assess(result,{})=='未能验证'
    for seeded in (False,True):
        calls,tests,modes,_=exercise([url],lambda *_:attempt.copy(),seed=seeded)
        saved=tests[url]
        assert saved['last_attempt']['error_category']==category,saved
        if seeded:
            assert saved['speed']==GOOD['speed'],saved
            assert saved.get('quality')==GOOD.get('quality'),saved
            assert modes[0]['mode']=='off',modes
        else:
            assert saved['inconclusive'] and assess(saved,{})=='未能验证',saved
assert classify_result({'httpStatus':404})['error_category']=='not_found'
assert classify_result({'httpStatus':403,'note':['login required']})['error_category']=='access_denied'
assert not classify_result({'httpStatus':403})['inconclusive']
assert not classify_result(GOOD).get('inconclusive')
print('PASS: classification, preserved prior measurements/manual state, unknown never qualifies')

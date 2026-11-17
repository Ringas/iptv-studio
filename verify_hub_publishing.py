"""用模拟接口验证说明同步、错误脱敏及无删除操作。"""
import io
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from scripts import docker_hub as hub

text=hub.description()
assert len(hub.SHORT_DESCRIPTION.encode('utf-8'))<=100
assert 'v1.1.1' in text and 'linux/amd64' in text and 'IPTV_LOG_LEVEL' in text
assert '(docs/' not in text and 'https://raw.githubusercontent.com/Ringas/iptv-studio/main/docs/screenshots/library.jpg' in text
calls=[]
def fake(method,path,token=None,data=None,missing_ok=False):
    calls.append((method,path,data))
    if path=='/v2/auth/token':return {'access_token':'temporary-test-bearer'}
    if method=='PATCH':return {}
    if path==hub.REPOSITORY:return {'full_description':text,'description':hub.SHORT_DESCRIPTION}
    if method=='DELETE':return {}
    return {} if sum(1 for method,_,_ in calls if method=='GET')==1 else None
with patch.dict(hub.os.environ,{'HUB_TOKEN':'private-test-token'}),patch.object(hub,'request',side_effect=fake):
    hub.main('update')
assert [method for method,_,_ in calls]==['POST','PATCH','GET']
assert not any(method=='DELETE' for method,_,_ in calls)
with patch.object(hub,'urlopen',side_effect=HTTPError('https://hub.docker.com/private',403,'bad',{},io.BytesIO(b'private-test-token'))):
    try:hub.request('PATCH',hub.REPOSITORY,token='private-test-token')
    except RuntimeError as error:assert '403' in str(error) and 'private-test-token' not in str(error)
    else:raise AssertionError('Expected a permission error')
print('通过：中文说明链接、版本、无删除操作与凭证错误脱敏')

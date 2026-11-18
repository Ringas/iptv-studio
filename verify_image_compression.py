"""压缩验证器必须拒绝内容变化、配置变化及损坏的 gzip。"""
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
from scripts.verify_image_compression import inspect,compare

def fixture(path,level=9,change=False,broken=False):
    files={}
    def add(data,media):
        digest=hashlib.sha256(data).hexdigest();files['blobs/sha256/'+digest]=data
        return {'digest':'sha256:'+digest,'size':len(data),'mediaType':media}
    data=(b'fixture stream and runtime contents\n'*1000)+(b'changed' if change else b'')
    compressed=gzip.compress(data,compresslevel=level,mtime=0)
    if broken:compressed=compressed[:-4]+b'bad!'
    layer=add(compressed,'application/vnd.oci.image.layer.v1.tar+gzip')
    cfg={'os':'linux','architecture':'amd64','config':{'Env':['TZ=Asia/Shanghai']},'rootfs':{'type':'layers','diff_ids':['sha256:'+hashlib.sha256(data).hexdigest()]}}
    config=add(json.dumps(cfg).encode(),'application/vnd.oci.image.config.v1+json')
    manifest=add(json.dumps({'schemaVersion':2,'config':config,'layers':[layer]}).encode(),'application/vnd.oci.image.manifest.v1+json')
    files['index.json']=json.dumps({'schemaVersion':2,'manifests':[manifest]}).encode()
    with tarfile.open(path,'w') as archive:
        for name,body in files.items():
            info=tarfile.TarInfo(name);info.size=len(body);archive.addfile(info,io.BytesIO(body))

with tempfile.TemporaryDirectory() as d:
    root=Path(d);a=root/'a.tar';b=root/'b.tar'
    fixture(a,6);fixture(b,9);before,after=inspect(a),inspect(b)
    assert compare(before,after)['uncompressed_layers_identical']
    for key,value in [('diff_ids',['sha256:changed']),('runtime_config',{'Env':['TZ=UTC']}),('gzip_header_flags',[0])]:
        altered=copy.deepcopy(after);altered[key]=value
        try:compare(before,altered);raise RuntimeError('invalid comparison accepted')
        except AssertionError:pass
    fixture(b,9,broken=True)
    try:inspect(b);raise AssertionError('broken gzip accepted')
    except (gzip.BadGzipFile,EOFError):pass
print('通过：gzip 压缩与解压摘要、配置一致性、高压缩标记及损坏拒绝。')

"""核对 OCI 压缩包：gzip 完整性、逐层原始摘要及运行配置，不进入镜像。"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile


def inspect(path):
    with tarfile.open(path,'r:*') as archive:
        def blob(descriptor):
            digest=descriptor['digest']
            if not re.fullmatch('sha256:[0-9a-f]{64}',digest):raise ValueError('无效 OCI 摘要')
            name='blobs/sha256/'+digest.split(':')[1]
            member=archive.getmember(name)
            if member.size!=descriptor['size']:raise ValueError('OCI 文件大小不一致')
            return name
        def document(descriptor):
            name=blob(descriptor)
            if descriptor['size']>8*1024*1024:raise ValueError('OCI 元数据过大')
            data=archive.extractfile(name).read()
            if 'sha256:'+hashlib.sha256(data).hexdigest()!=descriptor['digest']:raise ValueError('OCI 元数据摘要不一致')
            return json.loads(data)
        def images(descriptor):
            value=document(descriptor)
            if 'config' in value:
                cfg=document(value['config'])
                if (cfg.get('os'),cfg.get('architecture'))==('linux','amd64'):yield value,cfg
            else:
                for child in value.get('manifests',[]):yield from images(child)
        root=archive.getmember('index.json')
        if root.size>1024*1024:raise ValueError('OCI 索引过大')
        index=json.load(archive.extractfile(root))
        selected=[item for desc in index['manifests'] for item in images(desc)]
        if len(selected)!=1:raise ValueError('必须恰好包含一个 linux/amd64 镜像')
        manifest,config=selected[0]
        diffs=config['rootfs']['diff_ids']
        if len(diffs)!=len(manifest['layers']):raise ValueError('OCI 层数量不一致')
        total=0;headers=[]
        for descriptor,diff in zip(manifest['layers'],diffs):
            if not descriptor['mediaType'].endswith(('+gzip','.gzip')):raise ValueError('镜像层须使用兼容 gzip')
            name=blob(descriptor);total+=descriptor['size']
            compressed=hashlib.sha256()
            with archive.extractfile(name) as stream:
                header=stream.read(10);headers.append(header[8] if len(header)==10 else None)
                compressed.update(header)
                while chunk:=stream.read(1024*1024):compressed.update(chunk)
            if 'sha256:'+compressed.hexdigest()!=descriptor['digest']:raise ValueError('压缩层摘要不一致')
            original=hashlib.sha256();size=0
            with archive.extractfile(name) as stream,gzip.GzipFile(fileobj=stream) as decoded:
                while chunk:=decoded.read(1024*1024):
                    size+=len(chunk)
                    if size>4*1024**3:raise ValueError('解压层超过大小限制')
                    original.update(chunk)
            if 'sha256:'+original.hexdigest()!=diff:raise ValueError('解压层摘要不一致')
        return {'compressed_layer_bytes':total,'diff_ids':diffs,'runtime_config':config['config'],
                'gzip_header_flags':headers,'layers':len(diffs)}


def compare(before,after):
    assert before['diff_ids']==after['diff_ids'],'压缩改变了原始层内容或元数据'
    assert before['runtime_config']==after['runtime_config'],'压缩改变了运行配置'
    assert all(flag==2 for flag in after['gzip_header_flags']),'高压缩层未使用 gzip 最佳压缩标记'
    a,b=before['compressed_layer_bytes'],after['compressed_layer_bytes']
    assert b<=a,'高压缩包反而增大，拒绝发布'
    return {'default_gzip_bytes':a,'gzip9_bytes':b,'saved_bytes':a-b,
            'saved_percent':round((a-b)/a*100,3),'layers':after['layers'],
            'uncompressed_layers_identical':True,'runtime_config_identical':True}


if __name__=='__main__':
    result=compare(inspect(sys.argv[1]),inspect(sys.argv[2]))
    Path(sys.argv[3]).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))

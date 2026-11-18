"""构建验证用：对比保留运行文件、许可与配置，不进入发行镜像。"""
import hashlib
import json
from pathlib import Path
import re
import sys

def snapshot():
    result={}
    for top in ('usr','etc','bin','lib','lib64'):
        root=Path('/')/top
        paths=[root] if root.is_symlink() else root.rglob('*') if root.exists() else []
        for path in paths:
            if str(path) in ('/etc/hosts','/etc/hostname','/etc/resolv.conf'):continue
            if path.is_symlink():result[str(path)]={'link':str(path.readlink())}
            elif path.is_file():
                result[str(path)]={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return result

def allowed_removal(path):
    base='/usr/local/lib/python3.13/'
    return (path.startswith(tuple(base+p+'/' for p in ('pydoc_data','turtledemo','__phello__')))
            or path in (base+'turtle.py',base+'__hello__.py')
            or bool(re.fullmatch(re.escape(base)+r'config-[^/]+/[^/]+\.(a|o)',path)))

def compare(before,after):
    removed=sorted(before.keys()-after.keys())
    changed=sorted(p for p in before.keys()&after.keys() if before[p]!=after[p])
    added=sorted(after.keys()-before.keys())
    assert not added,('运行环境意外新增文件',added)
    assert not changed,('保留运行组件发生变化',changed)
    assert all(allowed_removal(p) for p in removed),('存在未经允许的删除',removed)
    return {'removed_files':removed,'removed_file_bytes':sum(before[p].get('bytes',0) for p in removed),
            'retained_files_identical':True,'retained_file_count':len(after)}

if __name__=='__main__':
    if sys.argv[1]=='snapshot':result=snapshot()
    else:result=compare(*(json.loads(Path(p).read_text()) for p in sys.argv[2:4]))
    print(json.dumps(result,ensure_ascii=False,indent=2))

"""设置行不再借助 label 隐式切换，同时保留可访问名称与说明。"""
from html.parser import HTMLParser
from pathlib import Path
class Check(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack=[];self.inputs={};self.ids=set()
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.add(attrs['id'])
        if attrs.get('id') in ('autoProbe','strictMode'):
            self.inputs[attrs['id']]=(attrs,list(self.stack))
        if tag not in ('input','meta','link','br','img','hr','source'):self.stack.append(tag)
    def handle_endtag(self,tag):
        if tag in self.stack:
            index=len(self.stack)-1-self.stack[::-1].index(tag)
            del self.stack[index:]
check=Check();check.feed(Path('index.html').read_text())
assert set(check.inputs)=={'autoProbe','strictMode'}
for attrs,ancestors in check.inputs.values():
    assert 'label' not in ancestors
    assert attrs['role']=='switch' and attrs['type']=='checkbox'
    assert attrs['aria-labelledby'] in check.ids and attrs['aria-describedby'] in check.ids
assert 'TZ:' not in Path('compose.yaml').read_text()
assert 'TZ=Asia/Shanghai' in Path('Dockerfile').read_text()
print('通过：开关仅自身可点击、键盘与辅助名称保留，默认 Compose 无时区参数')

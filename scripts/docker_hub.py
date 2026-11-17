"""维护本项目 Docker Hub 说明与已获授权清理的旧标签，不输出凭证。"""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://hub.docker.com'
REPOSITORY = '/v2/repositories/talktoo/iptv-studio/'


def request(method, path, token=None, data=None, missing_ok=False):
    headers = {'Accept': 'application/json', 'User-Agent': 'IPTV-Studio-Release'}
    if token: headers['Authorization'] = 'Bearer '+token
    body = None
    if data is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(data).encode()
    try:
        with urlopen(Request(BASE+path, data=body, headers=headers, method=method), timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        status = error.code
        error.close()
        if missing_ok and status == 404: return None
        raise RuntimeError(f'Docker Hub 操作失败：HTTP {status}（{method} {path}），请检查凭证权限') from None


def authenticate():
    secret = os.environ.get('HUB_TOKEN')
    if not secret: raise RuntimeError('缺少 Docker Hub 发布凭证')
    result = request('POST', '/v2/auth/token', data={'identifier': 'talktoo', 'secret': secret})
    if not result.get('access_token'): raise RuntimeError('Docker Hub 未返回有效授权')
    return result['access_token']


def description():
    # README is the single source: absolute links work in Docker Hub too.
    readme = (ROOT/'README.md').read_text()
    def absolute(match):
        prefix, label, target = match.groups()
        if target.startswith(('https://', 'http://', '#')): return match[0]
        base = 'https://raw.githubusercontent.com/Ringas/iptv-studio/main/' if prefix else 'https://github.com/Ringas/iptv-studio/blob/main/'
        return f'{prefix}[{label}]({base}{target})'
    readme = re.sub(r'(!?)\[([^\]]*)\]\(([^)]+)\)', absolute, readme)
    tag = (ROOT/'.github/release-version').read_text().splitlines()[0]
    return f'当前稳定镜像：`talktoo/iptv-studio:{tag}`，同步提供 `latest`。平台：**linux/amd64**。\n\n'+readme


def main(action):
    token = authenticate()
    if action == 'check':
        request('GET', REPOSITORY, token)
        print('Docker Hub 认证及仓库读取检查通过')
    elif action == 'update':
        text = description()
        request('PATCH', REPOSITORY, token, data={'description': '自托管 IPTV 频道、多线路检测、节目单与统一订阅管理；默认上海时区，适合 NAS 和 Docker。', 'full_description': text})
        updated = request('GET', REPOSITORY, token)
        if updated.get('full_description') != text: raise RuntimeError('Docker Hub 说明保存后的内容不一致')
        print('Docker Hub 中文说明已更新并核对一致')
    elif action == 'remove-v1.1.0':
        # Intentionally fixed scope: never delete the repository, latest or other tags.
        path = REPOSITORY+'tags/v1.1.0/'
        if request('GET', path, token, missing_ok=True) is not None:
            request('DELETE', path, token)
        if request('GET', path, token, missing_ok=True) is not None: raise RuntimeError('旧版 v1.1.0 标签仍可访问')
        print('Docker Hub 旧版 v1.1.0 标签已删除并确认不可访问')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'update', 'remove-v1.1.0'])
    try: main(parser.parse_args().action)
    except RuntimeError as error: raise SystemExit(str(error)) from None

"""同步本项目 Docker Hub 中文说明，不输出凭证。"""
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
SHORT_DESCRIPTION = 'IPTV 频道与多线路检测、节目单、统一订阅管理，默认上海时区。'


def request(method, path, token=None, data=None):
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
        raise RuntimeError(f'Docker Hub 操作失败：HTTP {status}（{method} {path}），请检查凭证权限') from None


def authenticate():
    secret = os.environ.get('HUB_TOKEN')
    if not secret: raise RuntimeError('缺少 Docker Hub 发布凭证')
    result = request('POST', '/v2/auth/token', data={'identifier': 'talktoo', 'secret': secret})
    if not result.get('access_token'): raise RuntimeError('Docker Hub 未返回有效授权')
    return result['access_token']


def description():
    # Docker Hub has its own deployment guide; never copy the GitHub README.
    template = (ROOT/'docs/dockerhub.md').read_text()
    tag = (ROOT/'.github/release-version').read_text().splitlines()[0]
    if not re.fullmatch(r'v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)', tag):
        raise ValueError('无效的稳定版本号')
    compose = (ROOT/'compose.yaml').read_text().strip()
    if '{{VERSION}}' not in template or '{{COMPOSE}}' not in template:
        raise ValueError('Docker Hub 文档模板缺少版本或 Compose 占位符')
    return template.replace('{{VERSION}}', tag).replace('{{COMPOSE}}', compose)



def main(action):
    token = authenticate()
    if action == 'check':
        request('GET', REPOSITORY, token)
        print('Docker Hub 认证及仓库读取检查通过')
    elif action == 'update':
        text = description()
        if len(SHORT_DESCRIPTION.encode('utf-8')) > 100: raise RuntimeError('Docker Hub 简介不能超过 100 字节')
        request('PATCH', REPOSITORY, token, data={'description': SHORT_DESCRIPTION, 'full_description': text})
        updated = request('GET', REPOSITORY, token)
        if updated.get('full_description') != text or updated.get('description') != SHORT_DESCRIPTION: raise RuntimeError('Docker Hub 说明保存后的内容不一致')
        print('Docker Hub 中文说明已更新并核对一致')



if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'update'])
    try: main(parser.parse_args().action)
    except RuntimeError as error: raise SystemExit(str(error)) from None

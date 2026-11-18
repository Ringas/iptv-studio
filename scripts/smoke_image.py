"""Verify the release image with a disposable data volume and loopback-only port."""
import json
import re
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener
import uuid


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def main(image, context):
    name = 'iptv-release-'+uuid.uuid4().hex[:10]
    opener = build_opener(ProxyHandler({}))
    try:
        docker('run', '-d', '--init', '--name', name, '-p', '127.0.0.1::8787', image)
        port = json.loads(docker('inspect', name))[0]['NetworkSettings']['Ports']['8787/tcp'][0]['HostPort']
        base = f'http://127.0.0.1:{port}'

        def request(path):
            with opener.open(base+path, timeout=5) as response:
                return response.read()

        def ready():
            for _ in range(30):
                try:
                    request('/health')
                    return
                except OSError:
                    time.sleep(1)
            raise RuntimeError('Container did not become healthy')

        ready()
        state = json.loads(request('/api/state'))
        assert state['sources'] == []
        settings = state['settings']
        assert settings['minutes'] == 480 and settings['auto_probe'] and settings['strict']
        assert settings['criteria'] == 'both' and settings['min_speed'] == 100 and settings['min_height'] == 720
        assert request('/live.m3u').startswith(b'#EXTM3U')
        assert b'playerDialog' in request('/')
        assert request('/hls.min.js')
        docker('exec', name, 'ffmpeg', '-version')
        docker('exec', name, 'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=128x72:rate=3', '-frames:v', '3', '-f', 'null', '-')
        docker('exec', name, 'node', '--version')
        docker('exec', name, 'node', '-e', 'const t=require("node:tls");if(!t.rootCertificates.length)process.exit(1);if(!Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai"}).format(new Date()))process.exit(1)')
        docker('exec', name, 'python3', '-c', 'import ssl,sqlite3,zlib,bz2,lzma,hashlib,socket,ctypes,zoneinfo;assert ssl.create_default_context().cert_store_stats()["x509_ca"]>0;assert socket.getaddrinfo("localhost",80);assert zoneinfo.ZoneInfo("Europe/London");assert hashlib.sha256(b"fixture").digest()')
        docker('exec', name, 'sh', '-c', 'id && ls -l /app >/dev/null')
        docker('exec', name, 'python3', '-c', 'import os,datetime;assert os.environ["TZ"]=="Asia/Shanghai";assert datetime.datetime.now().astimezone().utcoffset().total_seconds()==28800')
        docker('exec', name, 'node', '-e', 'if(new Date().getTimezoneOffset()!==-480)process.exit(1)')
        logs=docker('logs', name)
        logs=re.sub(r'\x1b\[[0-9;]*m','',logs)
        assert '频谱已启动' in logs and re.search(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}',logs)
        assert '+08:00' not in logs
        assert '/api/state' not in logs and '/health' not in logs
        expected = sorted(p.name for p in Path(context).iterdir() if p.name not in ('Dockerfile', '.dockerignore', 'runtime-prune.py'))
        actual = json.loads(docker('exec', name, 'python3', '-c', 'import json;from pathlib import Path;print(json.dumps(sorted(p.name for p in Path("/app").iterdir() if p.name!="data")))'))
        assert expected == actual, (expected, actual)
        assert 'LICENSE' in actual and 'hls-LICENSE.txt' in actual
        docker('exec', name, 'python3', '-c', 'from pathlib import Path;processes=[p for p in Path("/proc").glob("[0-9]*/status") if p.with_name("cmdline").read_bytes().split(bytes([0]))[:2]==[b"python3",b"app.py"]];assert processes;assert all(set(p.read_text().split("Uid:")[1].splitlines()[0].split())=={"1000"} for p in processes)')
        docker('exec', '-u', '1000:1000', name, 'python3', '-c', 'from pathlib import Path;Path("/app/data/restart-check").write_text("retained")')
        docker('restart', name)
        # Docker may allocate another host port when restarting an ephemeral binding.
        port = json.loads(docker('inspect', name))[0]['NetworkSettings']['Ports']['8787/tcp'][0]['HostPort']
        base = f'http://127.0.0.1:{port}'
        ready()
        docker('exec', name, 'python3', '-c', 'from pathlib import Path;assert Path("/app/data/restart-check").read_text()=="retained";assert Path("/app/data/hub.sqlite").is_file()')
        assert json.loads(request('/api/state'))['settings'] == settings
        info = json.loads(docker('image', 'inspect', image))[0]
        print(f'镜像启动与重启验证通过；平台={info["Os"]}/{info["Architecture"]}；体积={info["Size"]} bytes')
    except Exception:
        subprocess.run(['docker', 'logs', name], check=False)
        raise
    finally:
        subprocess.run(['docker', 'rm', '-fv', name], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])

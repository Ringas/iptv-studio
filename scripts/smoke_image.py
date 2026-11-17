"""Verify the release image with a disposable data volume and loopback-only port."""
import json
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
        expected = sorted(p.name for p in Path(context).iterdir() if p.name != 'Dockerfile')
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
        print(f'Image smoke checks passed; platform={info["Os"]}/{info["Architecture"]}; size={info["Size"]} bytes')
    except Exception:
        subprocess.run(['docker', 'logs', name], check=False)
        raise
    finally:
        subprocess.run(['docker', 'rm', '-fv', name], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])

"""Run isolated regressions before publishing; keep logs outside the source tree."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    node = shutil.which('node')
    decoder = shutil.which(os.getenv('FFMPEG_BIN', 'ffmpeg'))
    if not node or not decoder:
        raise SystemExit('Node.js and FFmpeg are required')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'NODE_BIN': node, 'FFMPEG_BIN': decoder}
    for path in [*ROOT.glob('*.py'), *ROOT.joinpath('scripts').glob('*.py')]:
        ast.parse(path.read_text(), filename=str(path))
    for path in [*ROOT.glob('*.mjs'), ROOT/'ui.js', ROOT/'demo/demo.js']:
        subprocess.run([node, '--check', str(path)], check=True)
    tests = sorted(p for p in ROOT.glob('verify*') if p.suffix in ('.py', '.mjs', '.cjs')
                   and p.name != 'verify_route_consistency.cjs')
    with tempfile.TemporaryDirectory(prefix='iptv-release-tests-') as logs:
        for path in tests:
            command = [sys.executable if path.suffix == '.py' else node, str(path)]
            try:
                result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=240)
            except subprocess.TimeoutExpired as error:
                print((error.stdout or b'').decode(errors='replace'))
                print((error.stderr or b'').decode(errors='replace'))
                raise SystemExit(f'TIMEOUT: {path.name}')
            Path(logs, path.name+'.log').write_bytes(result.stdout+result.stderr)
            if result.returncode:
                print((result.stdout+result.stderr).decode(errors='replace'))
                raise SystemExit(f'FAIL: {path.name}')
            print(f'PASS: {path.name}', flush=True)
    print(f'All {len(tests)} isolated regression scripts passed.')


if __name__ == '__main__':
    main()

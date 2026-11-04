"""Build the release ZIP and Docker context from the runtime allowlist."""
import argparse
import hashlib
import re
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package(tag, destination):
    if not re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", tag):
        raise ValueError("Expected a stable release tag, e.g. v1.0.0")
    destination.mkdir(parents=True, exist_ok=False)
    context = destination / "context"
    context.mkdir()
    rules = (ROOT / ".dockerignore").read_text().splitlines()
    if rules[0] != "*" or any(not re.fullmatch(r"![A-Za-z0-9_.-]+", r) for r in rules[1:]):
        raise ValueError("Expected an explicit allowlist in .dockerignore")
    allowed = sorted(r[1:] for r in rules[1:])
    for name in allowed:
        if name.startswith((".", "verify", "test")) or name.endswith((".sqlite", ".db", ".log", ".m3u")):
            raise ValueError(f"Unexpected runtime file: {name}")
        shutil.copyfile(ROOT / name, context / name)
    zip_path = destination / f"iptv-studio-{tag}.zip"
    with zipfile.ZipFile(zip_path, "x") as archive:
        def add(name, data):
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        for name in allowed:
            add(name, (context / name).read_bytes())
        add(".dockerignore", (ROOT / ".dockerignore").read_bytes())
        compose = (ROOT / "compose.yaml").read_text()
        image = "talktoo/iptv-studio:latest"
        if compose.count(image) != 1:
            raise ValueError("Check the published image in compose.yaml")
        add("compose.yaml", compose.replace(image, f"talktoo/iptv-studio:{tag}").encode())
        add("README.md", f"# IPTV Studio {tag}\n\n在解压目录执行 `docker compose up -d`，然后打开 http://服务器局域网IP:8788 。订阅和设置保存在 ./data。升级请沿用原数据卷。\n".encode())
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.testzip() is None
        assert sorted(archive.namelist()) == sorted(allowed + [".dockerignore", "compose.yaml", "README.md"])
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (destination / "SHA256SUMS").write_text(f"{digest}  {zip_path.name}\n", encoding="ascii")
    print(f"Release: {zip_path.stat().st_size} bytes; Docker context: {len(allowed)} files")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    package(args.tag, args.destination)

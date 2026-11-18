"""Build-only pruning: keep ELF dependencies, Python extensions and runtime data."""
import argparse
from collections import defaultdict, deque
import fnmatch
import json
import os
from pathlib import Path
import shutil
import struct


REMOVED_DIRS = ('usr/lib/apt', 'usr/lib/dpkg', 'usr/lib/systemd', 'usr/lib/udev', 'usr/local/lib/pkgconfig')
PYTHON_NONRUNTIME_DIRS = ('pydoc_data', 'turtledemo', '__phello__')
PYTHON_NONRUNTIME_FILES = ('turtle.py', '__hello__.py')


def resolve(root, path):
    parts = deque(path.relative_to(root).parts)
    current = []
    links = 0
    while parts:
        part = parts.popleft()
        if part in ('', '.'):
            continue
        if part == '..':
            if not current:
                raise ValueError('运行目录中的链接越界')
            current.pop()
            continue
        candidate = root.joinpath(*current, part)
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise ValueError('运行目录中的链接循环')
            target = Path(os.readlink(candidate))
            if target.is_absolute():
                current = []
                target = target.relative_to('/')
            parts.extendleft(reversed(target.parts))
        else:
            current.append(part)
    return root.joinpath(*current)


def elf_needed(path):
    with path.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            return None
        stream.seek(0)
        data = stream.read()
    bits, order = data[4:6]
    if bits not in (1, 2) or order not in (1, 2):
        raise ValueError(f'不支持的 ELF 格式：{path.name}')
    endian = '<' if order == 1 else '>'
    header = struct.unpack_from(endian + ('HHIIIIIHHHHHH' if bits == 1 else 'HHIQQQIHHHHHH'), data, 16)
    segments = []
    interpreter = None
    for index in range(header[9]):
        item = struct.unpack_from(endian + ('IIIIIIII' if bits == 1 else 'IIQQQQQQ'), data, header[4] + index * header[8])
        kind, offset, address, size = (item[0], item[1], item[2], item[4]) if bits == 1 else (item[0], item[2], item[3], item[5])
        segments.append((kind, offset, address, size))
        if kind == 3:
            interpreter = data[offset:offset + size].rstrip(b'\0').decode()
    needed, strings = [], None
    entry_size = 8 if bits == 1 else 16
    for kind, offset, address, size in segments:
        if kind != 2:
            continue
        for pos in range(offset, offset + size, entry_size):
            key, value = struct.unpack_from(endian + ('iI' if bits == 1 else 'qQ'), data, pos)
            if key == 0:
                break
            if key == 1:
                needed.append(value)
            if key == 5:
                strings = value
    if not needed:
        return set(), interpreter
    for kind, offset, address, size in segments:
        if kind == 1 and strings is not None and address <= strings < address + size:
            table = offset + strings - address
            return {data[table + value:data.index(b'\0', table + value)].decode() for value in needed}, interpreter
    raise ValueError(f'ELF 依赖字符串表不可用：{path.name}')


def prune(root):
    root = root.absolute()
    if root == Path('/') or root.is_symlink():
        raise ValueError('只能裁剪独立构建目录')
    for name in ('usr/local/bin/node', 'usr/local/bin/ffmpeg', 'usr/local/bin/python3.13'):
        if not (root / name).is_file():
            raise ValueError(f'缺少必要运行组件：{name}')
    all_paths = sorted(root.rglob('*'))
    library_dirs = {root / 'usr/lib', root / 'usr/lib64'}
    library_dirs.update(p for p in (root / 'usr/lib').glob('*-linux-gnu') if p.is_dir())
    python = root / 'usr/local/lib/python3.13'
    excluded_dirs = [root / name for name in REMOVED_DIRS]
    excluded_dirs += [python / name for name in PYTHON_NONRUNTIME_DIRS]
    extensions = root / 'usr/local/lib/python3.13/lib-dynload'
    excluded_files = {p for pattern in ('_test*.so', '_ctypes_test*.so', '_xxtestfuzz*.so', 'xxlimited*.so', 'xxsubtype*.so') for p in extensions.glob(pattern)}
    # The image already removes tkinter and has no Tcl/Tk libraries.
    if not (root / 'usr/local/lib/python3.13/tkinter').exists():
        excluded_files.update(extensions.glob('_tkinter*.so'))
        excluded_files.update(python / name for name in PYTHON_NONRUNTIME_FILES if (python / name).is_file())
    else:
        # A future image with Tk must retain its turtle GUI as well.
        excluded_dirs = [p for p in excluded_dirs if p != python / 'turtledemo']
    # Compiler/linker artifacts are never imported by the Python runtime.
    excluded_files.update(p for pattern in ('config-*/*.a','config-*/*.o') for p in python.glob(pattern) if p.is_file())
    excluded = excluded_dirs + list(excluded_files)
    index, metadata, candidates, roots = defaultdict(set), {}, set(), set()
    for path in all_paths:
        if any(path == d or d in path.parents for d in excluded):
            continue
        target = resolve(root, path)
        if not target.is_file():
            continue
        index[path.name].add(target)
        if target not in metadata:
            metadata[target] = elf_needed(target)
        if metadata[target] is None:
            continue
        shared = path.parent in library_dirs and fnmatch.fnmatch(path.name, '*.so*')
        if shared:
            candidates.add(path)
        if not shared or fnmatch.fnmatch(path.name, 'libnss_*.so*'):
            roots.add(target)
    for name in ('usr/local/bin/node', 'usr/local/bin/ffmpeg', 'usr/local/bin/python3.13'):
        if metadata.get(resolve(root, root / name)) is None:
            raise ValueError(f'必要组件不是 ELF：{name}')
    # All retained executables/extensions/plugins are roots. NSS uses dlopen.
    keep, queue = set(), list(roots)
    while queue:
        path = queue.pop()
        if path in keep:
            continue
        keep.add(path)
        info = metadata.get(path)
        if info is None:
            raise ValueError(f'运行依赖不是可解析的 ELF：{path.name}')
        names, interpreter = info
        if interpreter:
            target = resolve(root, root / interpreter.lstrip('/'))
            if target not in metadata:
                raise ValueError(f'缺少动态加载器：{interpreter}')
            queue.append(target)
        for name in names:
            matches = index.get(name, set())
            if not matches:
                raise ValueError(f'缺少运行依赖：{path.name} -> {name}')
            queue.extend(matches)
    # No deletion happens until the complete dependency graph has been checked.
    removed = [p for p in candidates if resolve(root, p) not in keep]
    removed_bytes = sum(p.stat().st_size for p in removed if not p.is_symlink())
    removed_names = sorted(str(p.relative_to(root)) for p in removed)
    for path in removed:
        path.unlink()
    for path in excluded_files:
        removed_bytes += path.stat().st_size
        path.unlink()
    removed_dirs = []
    for path in excluded_dirs:
        if path.exists():
            removed_dirs.append(str(path.relative_to(root)))
            removed_bytes += sum(p.stat().st_size for p in path.rglob('*') if p.is_file() and not p.is_symlink())
            shutil.rmtree(path)
    docs = root / 'usr/share/doc'
    for path in docs.rglob('*'):
        if (path.is_file() or path.is_symlink()) and not path.name.lower().startswith(('copyright', 'license', 'copying', 'notice')):
            removed_bytes += 0 if path.is_symlink() else path.stat().st_size
            path.unlink()
    report = {'removed_bytes': removed_bytes, 'removed_libraries': removed_names,
              'removed_test_extensions': sorted(p.name for p in excluded_files if p.suffix == '.so'),
              'removed_nonruntime_files': sorted(str(p.relative_to(root)) for p in excluded_files if p.suffix != '.so'),
              'removed_nonruntime_dirs': sorted(removed_dirs),
              'kept_elf_files': len(keep)}
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    prune(parser.parse_args().root)

"""Prepare a newly mounted data directory, then run the container unprivileged."""
import os
import sys
from pathlib import Path

if os.geteuid() == 0:
    data = Path('/app/data')
    if data.lstat().st_uid == 0 and data.is_dir() and not data.is_symlink() and not any(data.iterdir()):
        os.chown(data, 1000, 1000)
    os.setgroups([])
    os.setgid(1000)
    os.setuid(1000)
os.execvp(sys.argv[1], sys.argv[1:])

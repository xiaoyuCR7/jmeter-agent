"""Real-process helper copied outside the repository by startup-lock tests."""
import json
import os
import sys
import time
from pathlib import Path


request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
from app.services.startup_lock import startup_file_lock
with startup_file_lock(Path(request['target']), timeout=5):
    Path(request['entered']).write_text(str(os.getpid()), encoding='utf-8')
    if request['mode'] == 'hold':
        deadline = time.monotonic() + 5
        while not Path(request['release']).exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if not Path(request['release']).exists():
            raise TimeoutError('parent did not release holder')
    Path(request['result']).write_text(str(time.monotonic()), encoding='utf-8')

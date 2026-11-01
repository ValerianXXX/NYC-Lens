"""Report the actual bounded interpreter, then wait for its owner's cancellation."""
import json
import os
from pathlib import Path
import sys
import time

home = Path(sys.argv[1])
temporary = home / 'engine-ready.pending'
temporary.write_text(json.dumps({'pid': os.getpid(), 'prefix': sys.prefix,
    'base_prefix': sys.base_prefix, 'executable': sys.executable}), encoding='utf-8')
temporary.replace(home / 'engine-ready.json')
while True:
    time.sleep(.05)

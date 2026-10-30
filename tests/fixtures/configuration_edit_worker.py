"""Test-only edit after the first real site, once per isolated synthetic home."""
import hashlib
import json
import os
from pathlib import Path

from nyc_lens import comparison
from nyc_lens.analysis_worker import main

original_analyze = comparison.analyze_site


def analyze_and_edit(conn, **kwargs):
    result = original_analyze(conn, **kwargs)
    home = Path(os.environ['NYC_LENS_HOME'])
    marker = home / 'synthetic-config-edit.json'
    if not marker.exists():
        path = home / 'config/business_taxonomy.yml'
        original = path.read_bytes()
        changed = original.replace(b'overture_basic_categories: [restaurant]', b'overture_basic_categories: [cafe]', 1)
        if changed == original:
            raise AssertionError('Expected synthetic restaurant rule was absent')
        path.write_bytes(changed)
        with marker.open('x', encoding='utf8') as stream:
            json.dump({'completed_first_site': result['site_id'], 'worker_pid': os.getpid(),
                'before_sha256': hashlib.sha256(original).hexdigest(),
                'after_sha256': hashlib.sha256(changed).hexdigest()}, stream)
    return result


comparison.analyze_site = analyze_and_edit
main()

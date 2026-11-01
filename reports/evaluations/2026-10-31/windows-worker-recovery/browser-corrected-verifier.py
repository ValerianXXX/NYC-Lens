from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import duckdb
import httpx

root = Path(__file__).resolve().parent
home = Path(tempfile.gettempdir()) / 'nyc-worker-recovery-browser-20261031'
identifier = 'analysis_8d698ef54b904d05afece103e9c7deb6'
paths = list((home / 'data/cache/analysis-jobs').glob('*/analysis_*.json'))
assert len(paths) == 1
raw = paths[0].read_bytes()
receipt = json.loads(raw)
assert receipt['job_id'] == identifier and receipt['status'] == 'CANCELLED'
assert receipt['process']['process_stopped'] and receipt['database_cleanup_confirmed']
assert list(receipt['saved_states'].values()) == ['SUCCEEDED', 'CANCELLED', 'CANCELLED']
with httpx.Client(base_url='http://127.0.0.1:8795', timeout=10) as client:
    saved = client.get('/api/analysis-jobs/' + identifier)
    blocked = client.get('/api/analysis-jobs/' + identifier + '/result')
    child = client.get('/api/site/' + receipt['site_ids'][0])
assert saved.status_code == 200 and saved.json() == receipt
assert blocked.status_code == 503, blocked.text
assert blocked.json()['detail']['code'] == 'ANALYSIS_RESULT_NOT_READY'
assert 'result' not in blocked.json()
assert child.status_code == 200 and child.json()['site_id'] == receipt['site_ids'][0]
assert raw == paths[0].read_bytes()
with duckdb.connect(str(home / 'data/nyc_lens.duckdb'), read_only=True) as conn:
    sites = conn.execute('SELECT site_id,status FROM site_query ORDER BY created_at').fetchall()
    comparisons = conn.execute('SELECT comparison_id,status,site_ids_json FROM site_comparison').fetchall()
assert len(sites) == 2 and dict(sites) == {receipt['site_ids'][0]: 'SUCCEEDED', receipt['site_ids'][1]: 'CANCELLED'}
assert len(comparisons) == 1 and comparisons[0][1] == 'CANCELLED'
assert json.loads(comparisons[0][2]) == receipt['site_ids'][:1]
assert not list((home / 'data/cache/analysis-workers').glob('owned-*'))
state = (root / 'nyc-worker-recovery-browser-zh-20261031.txt').read_text(encoding='utf-8')
assert identifier in state and '数据库状态已核对' in state
record = {'observedAtUtc': datetime.now(timezone.utc).isoformat(), 'runtime': 'deae418552c4e251f665b4a69cbbac5e30f22e27',
    'classification': 'ACTUAL_BROWSER_HTTP_AND_WORKER_WITH_SYNTHETIC_DATA_AND_TEST_INJECTED_LONG_QUERY',
    'actions': ['Prepare quoted-address request', 'Confirm business/scope', 'Wait for second actual query marker',
        'Click Cancel', 'Reload page', 'Switch to Chinese', 'Check original task'],
    'receipt': receipt, 'receiptSha256': hashlib.sha256(raw).hexdigest(), 'receiptUnchangedAfterReads': True,
    'retainedChildHttp': child.status_code, 'incompleteComparisonHttp': blocked.status_code,
    'siteStates': sites, 'comparisonStates': comparisons, 'jobCount': len(paths), 'workerDirectoriesRemaining': 0,
    'automaticRetry': False, 'independentHuman': False, 'newModelCalls': 0,
    'limitations': ['One desktop browser', 'No real geographic data', 'No independent users', 'Fault-injected computation'],
    'screenshot': 'nyc-worker-recovery-browser-zh-20261031.jpg'}
with (root / 'nyc-worker-recovery-browser-verification-20261031.json').open('x', encoding='utf-8') as handle:
    json.dump(record, handle, ensure_ascii=False, indent=2)
print(json.dumps({'status': receipt['status'], 'cleanupConfirmed': True, 'savedStates': list(receipt['saved_states'].values()),
    'incompleteComparisonHttp': blocked.status_code, 'originalJobCount': 1}))

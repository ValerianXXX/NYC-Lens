"""Frozen CLI/resource/recovery contracts; only isolated synthetic databases."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from nyc_lens import analysis_execution, analysis_jobs, cli
from nyc_lens.analysis_jobs import AnalysisJobs, TERMINAL, stop_active_jobs
from nyc_lens.db import db_session
from nyc_lens.demo import DEMO_ADDRESSES, seed_demo

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('NYC_LENS_HOME', str(tmp_path))
    monkeypatch.delenv('NYC_LENS_DATABASE', raising=False)
    monkeypatch.setenv('NYC_LENS_AI_PROVIDER', 'disabled')
    seed_demo(tmp_path)
    yield tmp_path
    stop_active_jobs()


def arguments(command, key):
    args = [command, '--address', DEMO_ADDRESSES[0], '--catchments', '500m', '--no-export', '--request-id', key]
    if command == 'compare':
        args += ['--address', DEMO_ADDRESSES[1]]
    return args


def invoke(args):
    return runner.invoke(cli.app, args)


@pytest.mark.parametrize('command', ['analyze', 'compare'])
def test_cli_real_worker_replay_numeric_result_and_recovery_commands(home, command):
    key = uuid4().hex
    first = invoke(arguments(command, key))
    assert first.exit_code == 0, first.output
    result = json.loads(first.stdout)
    assert 'analysis_' + key in first.stderr
    repeated = invoke(arguments(command, key))
    assert repeated.exit_code == 0 and json.loads(repeated.stdout) == result
    receipt = AnalysisJobs().get('analysis_' + key)
    assert receipt['status'] == 'SUCCEEDED' and receipt['process']['process_stopped']
    assert receipt['database_cleanup_confirmed']
    assert receipt['database_settings']['memory_limit'] == '512.0 MiB'
    assert receipt['database_settings']['threads'] == 2
    for action in ['analysis-status', 'analysis-cancel']:
        observed = invoke([action, '--job-id', receipt['job_id']])
        assert observed.exit_code == 0 and json.loads(observed.stdout)['status'] == 'SUCCEEDED'
    recovered = invoke(['analysis-result', '--job-id', receipt['job_id']])
    assert recovered.exit_code == 0 and json.loads(recovered.stdout) == result
    if command == 'compare':
        assert [row['value'] for row in result['metric_comparisons']['direct_competitor_count']['values']] == [1., 3.]
    else:
        assert result['catchments']['500m']['metrics']['direct_competitor_count']['value'] == 1.
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == (1 if command == 'analyze' else 2)
    assert not list((home / 'data/cache/analysis-workers').glob('owned-*'))


@pytest.mark.parametrize('key,catchments', [('../wrong', '500m'), ('F' * 32, '500m'), ('', '500m'), ('1' * 32, 'sql')])
def test_invalid_cli_identity_or_scope_never_creates_analysis(home, key, catchments):
    args = arguments('analyze', key)
    args[args.index('--catchments') + 1] = catchments
    result = invoke(args)
    assert result.exit_code == 2
    assert json.loads(result.stdout)['error']['code'] == 'INVALID_ANALYSIS_INPUT'
    assert not list(AnalysisJobs().directory.glob('analysis_*.json'))
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == 0


def test_reused_cli_key_rejects_different_payload_and_preserves_original(home):
    key = uuid4().hex
    first = invoke(arguments('analyze', key))
    assert first.exit_code == 0, first.output
    args = arguments('analyze', key)
    args[args.index('--address') + 1] = DEMO_ADDRESSES[1]
    changed = invoke(args)
    assert changed.exit_code == 2 and json.loads(changed.stdout)['error']['code'] == 'INVALID_ANALYSIS_INPUT'
    assert AnalysisJobs().result('analysis_' + key) == json.loads(first.stdout)
    with db_session() as conn:
        assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == 1


@pytest.mark.parametrize('command,config_key', [('samples', 'sites'), ('regional-samples', 'regional_sites')])
def test_sample_commands_use_workers_and_export_only_completed_results(home, monkeypatch, command, config_key):
    monkeypatch.setattr(cli, 'test_sites_config', lambda: {config_key:[{'id':'synthetic-only','address':DEMO_ADDRESSES[0]}]})
    if command == 'samples':
        # Keep the test's configured sample on bounded geometric catchments;
        # the real demo has no downloaded walking network.
        original = analysis_jobs.AnalysisJobs.start
        def start(self, operation, request, **kwargs):
            return original(self, operation, {**request, 'catchments':['500m']}, **kwargs)
        monkeypatch.setattr(analysis_jobs.AnalysisJobs, 'start', start)
    args = [command, '--sample', 'synthetic-only']
    if command == 'regional-samples': args += ['--catchments', '500m']
    observed = invoke(args)
    assert observed.exit_code == 0, observed.output
    receipts = list(AnalysisJobs().directory.glob('analysis_*.json'))
    assert len(receipts) == 1
    receipt = AnalysisJobs().get(receipts[0].stem)
    assert receipt['status'] == 'SUCCEEDED' and receipt['process']['process_stopped']
    values = json.loads(observed.stdout)
    assert len(values) == 1 and values[0]['sample_id'] == 'synthetic-only'
    assert values[0]['site_id'] == receipt['result_id']
    assert all(Path(value).is_file() for value in values[0]['exports'].values())


def test_sync_library_interrupt_waits_for_owned_cleanup_and_preserves_first_site(home, monkeypatch):
    run_sync = getattr(analysis_jobs, 'run_analysis_sync', None)
    assert callable(run_sync), 'A bounded synchronous library entry point is required'
    monkeypatch.setattr(analysis_execution, 'WORKER_SCRIPT', Path(__file__).parents[1] / 'fixtures/held_analysis_worker.py')
    original_sleep = time.sleep
    raised = False
    def interrupted(seconds):
        nonlocal raised
        if not raised and (home / 'held-worker-ready.json').exists():
            raised = True
            raise KeyboardInterrupt()
        original_sleep(seconds)
    monkeypatch.setattr(analysis_jobs.time, 'sleep', interrupted)
    key = uuid4().hex
    with pytest.raises(KeyboardInterrupt):
        run_sync('comparison', {'addresses':DEMO_ADDRESSES, 'catchments':['500m']}, request_id=key)
    receipt = AnalysisJobs().get('analysis_' + key)
    assert raised and receipt['status'] == 'CANCELLED' and receipt['process']['process_stopped']
    assert receipt['database_cleanup_confirmed']
    assert list(receipt['saved_states'].values()) == ['SUCCEEDED','CANCELLED','CANCELLED']


def test_separate_cli_process_can_observe_cancel_and_replay_a_failed_task(home):
    key = uuid4().hex
    owner = subprocess.Popen([sys.executable, str(Path(__file__).parents[1] / 'fixtures/cli_job_owner.py'), key],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    try:
        deadline = time.monotonic() + 20
        while not (home / 'held-worker-ready.json').exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (home / 'held-worker-ready.json').exists(), 'CLI owner never started the bounded synthetic comparison'
        def external(action):
            return subprocess.run([sys.executable,'-m','nyc_lens.cli',action,'--job-id','analysis_'+key],capture_output=True,text=True,timeout=15)
        status = external('analysis-status')
        assert status.returncode == 0 and json.loads(status.stdout)['status'] == 'RUNNING'
        cancelled = external('analysis-cancel')
        assert cancelled.returncode == 0 and json.loads(cancelled.stdout)['status'] in {'CANCELLING','CANCELLED'}
        stdout, stderr = owner.communicate(timeout=15)
        assert owner.returncode == 1 and json.loads(stdout)['error']['status'] == 'CANCELLED'
        receipt = AnalysisJobs().get('analysis_' + key)
        assert receipt['process']['process_stopped'] and receipt['database_cleanup_confirmed']
        assert list(receipt['saved_states'].values()) == ['SUCCEEDED','CANCELLED','CANCELLED']
        result = external('analysis-result')
        assert result.returncode == 1 and json.loads(result.stdout)['error']['code'] == 'ANALYSIS_RESULT_NOT_READY'
        replay = invoke(arguments('compare',key))
        assert replay.exit_code == 1 and json.loads(replay.stdout)['error']['status'] == 'CANCELLED'
        with db_session() as conn:
            assert conn.execute('SELECT count(*) FROM site_query').fetchone()[0] == 2
    finally:
        if owner.poll() is None:
            AnalysisJobs().cancel('analysis_' + key)
            try: owner.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                owner.kill(); owner.communicate(timeout=5)


def test_missing_task_reports_a_structured_error_without_starting_one(home):
    result = invoke(['analysis-result','--job-id','analysis_' + 'a' * 32])
    assert result.exit_code == 1 and json.loads(result.stdout)['error']['code'] == 'ANALYSIS_JOB_NOT_FOUND'
    assert not list(AnalysisJobs().directory.glob('analysis_*.json'))


@pytest.mark.parametrize('command', ['analyze', 'compare'])
def test_default_export_after_worker_retains_report_files_and_saved_result(home, command):
    key = uuid4().hex
    args = arguments(command, key)
    args.remove('--no-export')
    result = invoke(args)
    assert result.exit_code == 0, result.output
    value = json.loads(result.stdout)
    assert all(Path(path).is_file() for path in value['exports'].values())
    saved = AnalysisJobs().result('analysis_' + key)
    field = 'site_id' if command == 'analyze' else 'comparison_id'
    assert value[field] == saved[field]
    # Both commands already export JSON; comparison's CLI export set has no
    # HTML key. Preserve its existing JSON/CSV/XLSX contract.
    assert 'SYNTHETIC' in Path(value['exports']['json']).read_text(encoding='utf8')


def test_new_cli_request_refreshes_registry_but_replay_keeps_original_registration(home):
    import yaml
    source = home / 'config/sources.yml'
    config = yaml.safe_load(source.read_text(encoding='utf8'))
    def edit(marker):
        config['sources']['overture_places']['known_limitations'] = marker
        source.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf8')
    def registered():
        with db_session() as conn:
            return conn.execute("SELECT known_limitations FROM source_registry WHERE source_id='overture_places'").fetchone()[0]
    edit('SYNTHETIC first CLI configuration')
    key = uuid4().hex
    first = invoke(arguments('analyze',key))
    assert first.exit_code == 0, first.output
    assert registered() == 'SYNTHETIC first CLI configuration'
    edit('SYNTHETIC next CLI configuration')
    replay = invoke(arguments('analyze',key))
    assert replay.exit_code == 0 and json.loads(replay.stdout) == json.loads(first.stdout)
    assert registered() == 'SYNTHETIC first CLI configuration'
    following = invoke(arguments('analyze',uuid4().hex))
    assert following.exit_code == 0, following.output
    assert registered() == 'SYNTHETIC next CLI configuration'

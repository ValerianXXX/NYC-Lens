"""Same hand-computed synthetic inputs through a selected checkout's real engine."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output; preserve earlier observations.')
    repository = args.repository.resolve()
    sys.path.insert(0, str(repository / 'src'))
    fixture_path = ROOT / 'tests/unit/test_census_input_contract.py'
    fixture = runpy.run_path(str(fixture_path))
    engine, estimate, population = (fixture[name] for name in ('engine', 'estimate', 'population'))
    G0, G1, G2, T0, T1 = (fixture[name] for name in ('G0', 'G1', 'G2', 'T0', 'T1'))
    observations = []
    for name in ['count', 'foreign-source', 'weighted-summary', 'tract-denominator', 'tract-population',
                 'missing-area', 'missing-component', 'health-geometry']:
        generator = fixture['inputs'].__wrapped__()
        conn, weights = next(generator)
        try:
            if name in ('count', 'foreign-source', 'weighted-summary', 'tract-population'):
                population(conn)
            if name == 'foreign-source':
                estimate(conn, 'other-source', G0, 900, source='synthetic_other_survey')
            if name in ('count', 'foreign-source'):
                result = engine._acs_aggregate(conn, weights, 'B01003_001')
                expected = {'value': 100, 'evidence': ['b0', 'b1', 'p0', 'p1']}
            elif name == 'weighted-summary':
                estimate(conn, 'm0', G0, 20000, 'B19013_001')
                estimate(conn, 'm1', G1, 80000, 'B19013_001')
                result = engine._acs_aggregate(conn, weights, 'B19013_001', median=True)
                expected = {'value': 50000, 'evidence': ['b0', 'b1', 'm0', 'm1', 'p0', 'p1']}
            elif name == 'tract-denominator':
                estimate(conn, 'ct0', T0, 400, 'B15003_001', 'tract')
                estimate(conn, 'ct1', T1, 300, 'B15003_001', 'tract')
                conn.execute("INSERT INTO geo_boundary SELECT 'other-boundary',geography_id,geography_type,geom_wkt,900,'synthetic_other_boundary','synthetic-other' FROM geo_boundary WHERE boundary_id='b0'")
                result = engine._acs_aggregate(conn, weights, 'B15003_001')
                expected = {'value': 175, 'evidence': ['b0', 'b1', 'b2', 'ct0', 'ct1']}
            elif name == 'tract-population':
                estimate(conn, 'p2', G2, 900)
                estimate(conn, 'mt0', T0, 20000, 'B19013_001', 'tract')
                estimate(conn, 'mt1', T1, 80000, 'B19013_001', 'tract')
                result = engine._acs_aggregate(conn, weights, 'B19013_001', median=True)
                expected = {'value': 30000, 'evidence': ['b0', 'b1', 'b2', 'mt0', 'mt1', 'p0', 'p1', 'p2']}
            elif name == 'missing-area':
                estimate(conn, 'p0', G0, 100)
                result = engine._acs_aggregate(conn, weights, 'B01003_001')
                expected = {'value': None, 'evidence': ['b0', 'b1', 'p0']}
            elif name == 'missing-component':
                estimate(conn, 'age0', G0, 20, 'B01001_003')
                estimate(conn, 'age1', G1, 20, 'B01001_003')
                result = engine._sum_age_variables(conn, weights, [3, 4])
                expected = {'value': None, 'evidence': ['age0', 'age1', 'b0', 'b1']}
            else:
                for identifier, geoid, value in [('h0', T0, 20), ('h1', T1, 40)]:
                    conn.execute("INSERT INTO health_indicator VALUES (?,?,'SYNTHETIC',?,2,.7,'cdc_places_2025_tract','synthetic-health')", [identifier, geoid, value])
                estimate(conn, 'pt0', T0, 100, kind='tract')
                estimate(conn, 'pt1', T1, 200, kind='tract')
                result = engine._tract_health_aggregate(conn, weights, 'SYNTHETIC')
                expected = {'value': 30, 'evidence': ['b0', 'b1', 'h0', 'h1', 'pt0', 'pt1', 't0', 't1']}
            actual = {'value': result[0], 'evidence': sorted(set(result[2]))}
            observations.append({'scenario': name, 'expected': expected, 'actual': actual, 'passed': actual == expected})
        finally:
            generator.close()
    value = {'observed_at_utc': datetime.now(timezone.utc).isoformat(),
        'checkout_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip(),
        'fixture_lf_sha256': hashlib.sha256(fixture_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        'evaluator_lf_sha256': hashlib.sha256(Path(__file__).read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
        'mode': 'REAL_DUCKDB_AGGREGATION_SYNTHETIC_INPUTS', 'new_provider_calls': 0,
        'whole_metric_lineage_acceptance': False, 'observations': observations,
        'summary': {'passed': sum(row['passed'] for row in observations), 'scenarios': len(observations)}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as target:
        json.dump(value, target, indent=2)
        target.write('\n')
    print(json.dumps(value['summary']))
    return 0 if all(row['passed'] for row in observations) else 1


if __name__ == '__main__':
    raise SystemExit(main())

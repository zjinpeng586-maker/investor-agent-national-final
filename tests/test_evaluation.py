from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.evaluation import load_benchmark_suite, run_benchmark, summarize_results


APP_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'


def _stable_cases(result):
    return [(case['id'], case['passed'], case['actual'], case['error']) for case in result['cases']]


def test_suite_is_auditable_and_case_ids_are_unique():
    suite = load_benchmark_suite()
    assert suite['suite_id']
    assert suite['version']
    ids = [case['id'] for case in suite['cases']]
    assert len(ids) == len(set(ids))
    assert all(case.get('category') and 'expected' in case for case in suite['cases'])
    assert set(case['category'] for case in suite['cases']) == {
        'conversation', 'planner', 'text_to_sql', 'sql_security', 'rag_citation',
    }


def test_summary_is_computed_from_case_results():
    summary = summarize_results([
        {'category': 'planner', 'passed': True},
        {'category': 'planner', 'passed': False},
        {'category': 'planner', 'passed': True},
    ])
    assert summary['passed'] == 2
    assert summary['total'] == 3
    assert summary['pass_rate'] == 2 / 3
    assert summary['categories']['planner']['pass_rate'] == 2 / 3


def test_benchmark_executes_all_real_categories_and_serializes():
    result = run_benchmark()
    assert result['total'] == len(load_benchmark_suite()['cases'])
    assert all(case['duration_ms'] >= 0 for case in result['cases'])
    assert json.loads(json.dumps(result, ensure_ascii=False))['suite_id'] == result['suite_id']

    by_id = {case['id']: case for case in result['cases']}
    assert by_id['conv_four_turns']['actual']['metrics'] == ['net_profit']
    assert by_id['plan_hybrid_compare']['actual']['structured_intent'] == 'company_compare'
    assert by_id['sql_byd_2024_revenue']['actual']['rows'][0]['revenue'] == 7771.02
    assert by_id['sql_compare_2024']['actual']['rows'][1]['net_profit'] in {402.54, 507.45}
    assert all(by_id[name]['actual']['blocked'] for name in [
        'security_insert', 'security_update', 'security_delete', 'security_drop',
        'security_pragma', 'security_attach', 'security_multi_statement', 'security_system_table',
    ])
    assert by_id['security_legal_select']['actual'] == {'blocked': False, 'executable': True}
    assert by_id['rag_research_page']['actual']['pages'][0] == 1
    assert by_id['rag_overseas_page']['actual']['pages'][0] == 2
    assert by_id['rag_profit_reason_page']['actual']['pages'][0] == 5
    assert not ({3, 4} & set(by_id['rag_profit_reason_page']['actual']['pages']))
    assert by_id['rag_unrelated']['actual']['status'] == 'no_evidence'
    assert by_id['rag_numeric_without_reason']['actual']['status'] == 'no_evidence'


def test_case_failure_isolated_and_repeatability_is_deterministic():
    suite = load_benchmark_suite()
    broken = {
        **suite,
        'cases': [
            {'id': 'forced_error', 'category': 'unsupported', 'expected': {}},
            suite['cases'][5],
        ],
    }
    isolated = run_benchmark(broken)
    assert isolated['total'] == 2
    assert isolated['cases'][0]['passed'] is False
    assert isolated['cases'][0]['error']
    assert isolated['cases'][1]['passed'] is True

    first, second = run_benchmark(), run_benchmark()
    assert _stable_cases(first) == _stable_cases(second)


def test_evaluation_ui_runs_real_benchmark_and_exposes_details_download():
    app = AppTest.from_file(APP_PATH, default_timeout=60).run()
    app.radio[0].set_value('评测中心').run()
    assert '尚未运行当前环境 Benchmark' in '\n'.join(item.value for item in app.warning)
    assert not app.metric
    next(button for button in app.button if button.label == '运行本地 Benchmark').click().run(timeout=60)
    assert not app.exception
    result = app.session_state.benchmark_result
    assert result['total'] == len(result['cases'])
    assert any(metric.label == '总样本数' and int(metric.value) == result['total'] for metric in app.metric)
    assert any('Case ID' in frame.value.columns for frame in app.dataframe)
    assert any(button.label == '下载 Benchmark JSON' for button in app.download_button)

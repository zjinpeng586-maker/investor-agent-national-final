"""All exchange requests are mocked: transport and malformed bodies differ from empty."""
from types import SimpleNamespace

import pytest

from core import online_disclosure as online


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Test attempted real network access')
    monkeypatch.setattr(online.requests.Session, 'request', forbidden)


def mock_http(monkeypatch, text=None, status=200, error=None):
    def respond(*args, **kwargs):
        if error:
            raise error
        return SimpleNamespace(text=text, status_code=status)
    monkeypatch.setattr(online, '_session', lambda: SimpleNamespace(get=respond, post=respond))


def test_search_success_contains_required_contract(monkeypatch):
    mock_http(monkeypatch, '{"pageHelp":{"pageCount":1,"data":[{"TITLE":"测试科技2024年年度报告","URL":"/report.pdf","SECURITY_CODE":"600999","SECURITY_NAME":"测试科技"}]}}')
    result = online.search_disclosures_with_details('600999', '上交所', '年度报告', 2024)
    assert result['status'] == 'success'
    assert result['candidate_count'] == len(result['candidates']) == 1
    assert result['request_success_count'] == 1
    assert result['request_failure_count'] == 0
    assert result['query'] == '600999' and result['exchange'] == 'sse'
    assert result['year'] == 2024 and result['report_type'] == '年度报告'
    assert result['elapsed_sec'] >= 0 and result['diagnostics']['requests']
    assert '检索完成：共获取 1 条符合条件的公开披露。' in result['message']


@pytest.mark.parametrize('exchange,body,query', [
    ('上交所', '{"pageHelp":{"pageCount":1,"data":[]}}', '600999'),
    ('深交所', '{"totalSize":0,"data":[]}', '测试科技'),
])
def test_valid_empty_is_not_network_failure(monkeypatch, exchange, body, query):
    mock_http(monkeypatch, body)
    result = online.search_disclosures_with_details(query, exchange, year=2024)
    assert result['status'] == 'empty'
    assert result['request_success_count'] > 0
    assert result['request_failure_count'] == 0
    assert result['candidates'] == []
    assert '检索完成，但未找到' in result['message']


@pytest.mark.parametrize('body,status,error', [
    ('<html>Access denied</html>', 200, None),
    ('{"unexpected":"shape"}', 200, None),
    ('{"data":[],"success":false,"message":"API failure"}', 200, None),
    ('{"data":[],"code":500}', 200, None),
    ('{"data":["invalid row"]}', 200, None),
    ('', 503, None),
    ('', 200, TimeoutError('模拟请求超时')),
])
@pytest.mark.parametrize('exchange,query', [('上交所', '600999'), ('深交所', '测试科技')])
def test_invalid_responses_and_network_errors_fail(monkeypatch, body, status, error, exchange, query):
    mock_http(monkeypatch, body, status, error)
    result = online.search_disclosures_with_details(query, exchange, year=2024)
    assert result['status'] == 'failed'
    assert result['request_success_count'] == 0
    assert result['request_failure_count'] > 0
    assert result['candidate_count'] == 0
    assert '检索失败' in result['message']
    assert '未找到符合条件' not in result['message']


def test_partial_exchange_failure_keeps_other_exchange_candidates(monkeypatch):
    candidate = online.DisclosureItem('测试科技2024年年度报告', '001234', '测试科技', '2025-04-01',
                                      '深交所', 'https://disc.static.szse.cn/test.pdf')
    def fail(*args, **kwargs):
        raise TimeoutError('上交所暂不可用')
    monkeypatch.setattr(online, 'search_sse_with_details', fail)
    monkeypatch.setattr(online, 'search_szse_with_details', lambda *args, **kwargs:
                        ([candidate], {'steps': ['深交所请求完成'], 'requests': [{'status': '200', 'error': ''}]}))
    result = online.search_disclosures_with_details('测试科技', year=2024)
    assert result['status'] == 'success'
    assert result['candidates'] == [candidate]
    assert result['request_success_count'] == result['request_failure_count'] == 1


def test_partial_failure_with_valid_empty_is_empty(monkeypatch):
    monkeypatch.setattr(online, 'search_sse_with_details', lambda *args, **kwargs:
                        ([], {'requests': [{'status': 'EXCEPTION', 'error': 'timeout'}]}))
    monkeypatch.setattr(online, 'search_szse_with_details', lambda *args, **kwargs:
                        ([], {'requests': [{'status': '200', 'error': ''}]}))
    result = online.search_disclosures_with_details('测试科技', year=2024)
    assert result['status'] == 'empty'
    assert result['request_success_count'] == result['request_failure_count'] == 1


@pytest.mark.parametrize('query,exchange', [('', '自动判断'), ('未映射企业', '上交所'), ('600999', '不支持')])
def test_no_effective_response_is_failed(query, exchange):
    result = online.search_disclosures_with_details(query, exchange)
    assert result['status'] == 'failed' and not result['candidates']
    assert result['request_success_count'] == 0

"""Network-free negative tests for the public-facing PDF downloader."""
from threading import Event

import pytest

from core import downloads
from core.downloads import DownloadSafetyError, safe_download_pdf, validate_download_target
from core.online_disclosure import cache_pdf, download_pdf
from core.storage import workspace_context

URL = 'https://static.cninfo.com.cn/reports/annual.pdf'
PDF = b'%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n'


def _addr(ip):
    return (2 if ':' not in ip else 10, 1, 6, '', (ip, 443))


class Response:
    def __init__(self, data=PDF, status=200, headers=None, chunks=None, tick=None):
        self.status = status
        self.headers = headers or {'Content-Type': 'application/pdf'}
        self.chunks = iter(chunks if chunks is not None else [data])
        self.tick = tick
        self.closed = False
        self.connection = None

    def read1(self, amount, decode_content=False):
        if self.tick:
            self.tick()
        return next(self.chunks, b'')

    def close(self):
        self.closed = True


@pytest.fixture
def network(monkeypatch):
    calls, dns, queued, pools = [], [], [], []

    def resolver(host, port, **kwargs):
        dns.append(host)
        return [_addr('8.8.8.8')]

    class Pool:
        def __init__(self, host, **kwargs):
            self.host, self.kwargs, self.closed = host, kwargs, False
            pools.append(self)

        def request(self, method, path, **kwargs):
            calls.append((self.host, method, path, kwargs))
            if not queued:
                raise AssertionError('Unexpected network attempt')
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        def close(self):
            self.closed = True

    monkeypatch.setattr(downloads.socket, 'getaddrinfo', resolver)
    monkeypatch.setattr(downloads.urllib3, 'HTTPSConnectionPool', Pool)
    return dict(calls=calls, dns=dns, queued=queued, pools=pools)


@pytest.mark.parametrize('url', [
    'http://static.cninfo.com.cn/annual.pdf',
    'file:///etc/passwd',
    'https://127.0.0.1/annual.pdf',
    'https://169.254.169.254/latest/meta-data/',
    'https://static.cninfo.com.cn.evil.example/annual.pdf',
    'https://evilstatic.cninfo.com.cn/annual.pdf',
    'https://static.cninfo.com.cn@evil.example/annual.pdf',
    'https://user:password@static.cninfo.com.cn/annual.pdf',
    'https://static.cninfo.com.cn:8443/annual.pdf',
    'https://static.cninfo.com.cn./annual.pdf',
    'https://static.cninfo.com.cn\\@evil.example/annual.pdf',
    'https://static.cninfo.com.cn\n/annual.pdf',
])
def test_invalid_targets_are_rejected_before_dns_or_network(url, network):
    with pytest.raises(DownloadSafetyError, match='上传'):
        safe_download_pdf(url)
    assert not network['calls']
    assert not network['dns']


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '172.16.0.2', '192.168.1.2', '169.254.169.254',
                               '100.64.0.1', '::1', 'fe80::1', 'fc00::1', '::ffff:8.8.8.8', '224.0.0.1'])
def test_every_dns_answer_must_be_public(ip, network, monkeypatch):
    monkeypatch.setattr(downloads.socket, 'getaddrinfo', lambda *a, **kw: [_addr('8.8.8.8'), _addr(ip)])
    with pytest.raises(DownloadSafetyError, match='非公网'):
        safe_download_pdf(URL)
    assert not network['calls']


def test_tls_connection_is_pinned_to_validated_ip_and_never_resolves_host_again(network):
    response = Response()
    network['queued'].append(response)
    name, content = safe_download_pdf(URL)
    assert name == 'annual.pdf' and content == PDF
    assert network['dns'] == ['static.cninfo.com.cn']
    pool = network['pools'][0]
    assert pool.host == '8.8.8.8'
    assert pool.kwargs['server_hostname'] == 'static.cninfo.com.cn'
    assert pool.kwargs['assert_hostname'] == 'static.cninfo.com.cn'
    assert pool.kwargs['cert_reqs'] == 'CERT_REQUIRED'
    options = network['calls'][0][3]
    assert options['headers']['Host'] == 'static.cninfo.com.cn'
    assert options['redirect'] is False and options['retries'] is False
    assert options['preload_content'] is False and options['decode_content'] is False
    assert response.closed and pool.closed


def test_redirect_to_private_or_unknown_host_never_opens_second_connection(network):
    for target in ['https://169.254.169.254/latest/meta-data/', 'https://evil.example/file.pdf']:
        network['queued'].append(Response(status=302, headers={'Location': target}))
        before = len(network['calls'])
        with pytest.raises(DownloadSafetyError):
            safe_download_pdf(URL)
        assert len(network['calls']) == before + 1


def test_allowlisted_redirect_resolves_and_checks_all_addresses_again(network, monkeypatch):
    network['queued'].append(Response(status=302, headers={'Location': 'https://static.sse.com.cn/annual.pdf'}))
    calls = []

    def resolver(host, *a, **kw):
        calls.append(host)
        return [_addr('8.8.8.8' if host == 'static.cninfo.com.cn' else '10.0.0.1')]

    monkeypatch.setattr(downloads.socket, 'getaddrinfo', resolver)
    with pytest.raises(DownloadSafetyError, match='非公网'):
        safe_download_pdf(URL)
    assert calls == ['static.cninfo.com.cn', 'static.sse.com.cn']
    assert len(network['calls']) == 1


def test_redirect_limit_and_no_http_fallback(network):
    network['queued'].extend(Response(status=302, headers={'Location': URL}) for _ in range(4))
    with pytest.raises(DownloadSafetyError, match='重定向'):
        safe_download_pdf(URL)
    assert len(network['calls']) == 4
    network['queued'].append(Response(status=302, headers={'Location': URL.replace('https:', 'http:')}))
    before = len(network['calls'])
    with pytest.raises(DownloadSafetyError, match='HTTPS'):
        safe_download_pdf(URL)
    assert len(network['calls']) == before + 1


@pytest.mark.parametrize(('data', 'headers'), [
    (b'<html><a href="https://static.cninfo.com.cn/report.pdf">PDF</a></html>', {'Content-Type': 'text/html'}),
    (b'<html>fake %PDF-1.7\n%%EOF</html>', {'Content-Type': 'application/pdf'}),
    (b'%PDF-1.7\nincomplete', {'Content-Type': 'application/pdf'}),
    (PDF, {'Content-Type': 'application/pdf', 'Content-Encoding': 'gzip'}),
    (PDF, {'Content-Type': 'application/pdf', 'Content-Length': '9999999999'}),
    (PDF, {'Content-Type': 'application/pdf', 'Content-Length': '2'}),
])
def test_html_truncation_compression_and_inconsistent_lengths_are_rejected(data, headers, network):
    network['queued'].append(Response(data=data, headers=headers))
    with pytest.raises(DownloadSafetyError, match='上传'):
        safe_download_pdf(URL)
    assert len(network['calls']) == 1


def test_streaming_cap_stops_before_reading_unbounded_body(network, monkeypatch):
    monkeypatch.setattr(downloads, 'MAX_PDF_BYTES', 100)
    network['queued'].append(Response(chunks=[b'%PDF-1.7\n' + b'x' * 70, b'x' * 70, b'x' * 10000]))
    with pytest.raises(DownloadSafetyError, match='超过'):
        safe_download_pdf(URL)
    assert len(network['calls']) == 1


def test_total_time_budget_interrupts_slow_stream(network, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(downloads.time, 'monotonic', lambda: clock[0])

    def advance():
        clock[0] += 2.1

    network['queued'].append(Response(chunks=[b'%PDF-1.7\n', b'%%EOF'], tick=advance))
    with pytest.raises(DownloadSafetyError, match='总耗时'):
        safe_download_pdf(URL, timeout=2)
    assert len(network['calls']) == 1


def test_dns_has_its_own_bounded_wait(network, monkeypatch):
    release = Event()
    monkeypatch.setattr(downloads, 'DNS_TIMEOUT', 0.001)

    def slow_resolver(*args, **kwargs):
        release.wait(1)
        return [_addr('8.8.8.8')]

    monkeypatch.setattr(downloads.socket, 'getaddrinfo', slow_resolver)
    try:
        with pytest.raises(DownloadSafetyError, match='解析超时'):
            safe_download_pdf(URL)
    finally:
        release.set()
    assert not network['calls']


def test_tls_or_read_failure_does_not_invoke_old_downloader(network, monkeypatch):
    import core.online_disclosure as disclosure

    def forbidden(*args, **kwargs):
        raise AssertionError('legacy fallback called')

    monkeypatch.setattr(disclosure, '_download_binary_once', forbidden)
    monkeypatch.setattr(disclosure, '_download_pdf_bytes_strict', forbidden)
    network['queued'].append(TimeoutError('fixture timeout'))
    with pytest.raises(DownloadSafetyError, match='安全下载未完成'):
        download_pdf(URL)
    assert len(network['calls']) == 1


def test_cache_is_readonly_in_public_mode_and_unique_in_writable_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCIAL_DEPLOYMENT', 'public')
    with workspace_context('main', tmp_path / 'main'):
        with pytest.raises(PermissionError):
            cache_pdf(PDF, '../annual.pdf')
    assert not (tmp_path / 'main').exists()
    with workspace_context('main', tmp_path / 'main', allow_writes=True):
        first = cache_pdf(PDF, '../../annual.pdf')
        second = cache_pdf(PDF, '../../annual.pdf')
        assert first != second
        assert first.read_bytes() == second.read_bytes() == PDF
        assert first.parent == second.parent == tmp_path / 'main' / 'online_cache'
        assert first.is_relative_to(tmp_path / 'main')

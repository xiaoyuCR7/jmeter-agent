"""The CI gate is tested with an injected transport, never a live API."""
import importlib.util
import json
from pathlib import Path
import pytest


def gate_module():
    path = Path(__file__).resolve().parents[2] / '.github/scripts/performance_gate.py'
    assert path.exists(), 'CI needs a real executable gate, not nonexistent agent/tools routes'
    spec = importlib.util.spec_from_file_location('performance_gate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeAPI:
    def __init__(self, status='completed', verdict='pass', execution_id='run', artifact=b'timeStamp,elapsed\n1,10\n'):
        self.calls = []
        self.status, self.verdict, self.execution_id, self.artifact = status, verdict, execution_id, artifact

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.endswith('/execute'):
            return {'id': self.execution_id, 'status': 'pending'}
        if path == '/api/v1/executions/run':
            return {'id': 'run', 'status': self.status}
        if path.endswith('/report'):
            return {'execution_id': 'run', 'status': 'completed', 'summary': {'total_requests': 1},
                'sla_result': {'verdict': self.verdict, 'total_checks': 4}}
        if path.endswith('/result.jtl'):
            return self.artifact
        raise AssertionError(f'Unexpected real API contract: {method} {path}')


def test_ci_gate_uses_registered_plan_poll_report_and_artifact(tmp_path):
    module = gate_module()
    fake = FakeAPI()
    assert module.run_gate(fake, 'plan', tmp_path, timeout=1, poll_interval=0) == 'run'
    assert (tmp_path / 'result.jtl').read_bytes() == fake.artifact
    assert all('/agent/tools/' not in path for _, path, _ in fake.calls)


@pytest.mark.parametrize('kwargs', [{'verdict': 'fail'}, {'verdict': None}, {'status': 'failed'},
    {'execution_id': ''}, {'artifact': b''}])
def test_ci_gate_fails_closed(tmp_path, kwargs):
    module = gate_module()
    with pytest.raises((ValueError, RuntimeError, TimeoutError)):
        module.run_gate(FakeAPI(**kwargs), 'plan', tmp_path, timeout=0.05, poll_interval=0)


def test_ci_timeout_cancels_owned_execution(tmp_path):
    module = gate_module()
    fake = FakeAPI(status='running')
    def request(method, path, body=None):
        if path.endswith('/cancel'):
            fake.calls.append((method, path, body))
            return {'success': True}
        return fake(method, path, body)
    with pytest.raises(TimeoutError):
        module.run_gate(request, 'plan', tmp_path, timeout=0.01, poll_interval=0)
    assert ('POST', '/api/v1/executions/run/cancel', None) in fake.calls


def test_ci_main_returns_nonzero_on_sla_failure(monkeypatch, tmp_path):
    module = gate_module()
    monkeypatch.setenv('AGENT_URL', 'https://fixture.invalid')
    monkeypatch.setenv('AGENT_TOKEN', 'fixture-token')
    monkeypatch.setenv('AGENT_PLAN_ID', 'plan')
    monkeypatch.setattr(module, 'APIClient', lambda *args: FakeAPI(verdict='fail'))
    assert module.main(['--output', str(tmp_path)]) == 1


def test_examples_use_token_and_shared_gate():
    root = Path(__file__).resolve().parents[2]
    for relative in ('.github/workflows/jmeter-agent.yml', 'gitlab-ci-example.yml'):
        content = (root / relative).read_text(encoding='utf-8')
        assert 'performance_gate.py' in content
        assert 'AGENT_TOKEN' in content
        assert 'admin123' not in content and '/agent/tools/' not in content


def test_ci_client_passes_token_and_propagates_http_failures():
    from io import BytesIO
    from urllib.error import HTTPError
    module = gate_module()
    client = module.APIClient('https://fixture.invalid', 'fixture-token')
    calls = []
    def open_request(request, timeout):
        calls.append(request)
        if request.full_url.endswith('/reject'):
            raise HTTPError(request.full_url, 403, 'Forbidden', {}, None)
        return BytesIO(b'{"status":"completed"}')
    client.opener = type('FakeOpener', (), {'open': staticmethod(open_request)})()
    assert client('GET', '/api/v1/executions/run')['status'] == 'completed'
    assert calls[0].get_header('Authorization') == 'Bearer fixture-token'
    with pytest.raises(HTTPError):
        client('POST', '/reject')


def test_ci_examples_parse_as_yaml():
    import yaml
    root = Path(__file__).resolve().parents[2]
    github = yaml.safe_load((root / '.github/workflows/jmeter-agent.yml').read_text())
    gitlab = yaml.safe_load((root / 'gitlab-ci-example.yml').read_text())
    assert github['jobs']['performance-test']['timeout-minutes'] == 60
    assert gitlab['smoke-test']['allow_failure'] is False

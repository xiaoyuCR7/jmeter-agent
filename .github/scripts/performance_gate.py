"""Gate a reviewed, registered plan via the real REST API (stdlib only).

Required: AGENT_URL, AGENT_TOKEN, AGENT_PLAN_ID. Optional baseline execution ID.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class APIClient:
    def __init__(self, base_url, token):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username:
            raise ValueError('AGENT_URL must be an HTTP(S) URL without credentials')
        if not token:
            raise ValueError('AGENT_TOKEN is required')
        self.base_url, self.token = base_url.rstrip('/'), token
        self.opener = build_opener(NoRedirect())

    def __call__(self, method, path, body=None):
        request = Request(self.base_url + path, data=json.dumps(body).encode() if body is not None else None,
            method=method, headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=30) as response:
            content = response.read()
        return content if path.endswith('/result.jtl') else json.loads(content)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', value):
        raise ValueError('Missing or invalid plan/execution ID')
    return value


def run_gate(request, plan_id, output, timeout=3300, poll_interval=5, baseline_id=None):
    """Raise on API errors, unsuccessful execution, missing evidence or SLA failure."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    started = request('POST', f'/api/v1/test-plans/{identifier(plan_id)}/execute')
    execution_id = identifier(started.get('id'))
    deadline = time.monotonic() + timeout
    status_path = f'/api/v1/executions/{execution_id}'
    while True:
        execution = request('GET', status_path)
        status = execution.get('status')
        if status == 'completed':
            break
        if status not in {'pending', 'running'}:
            raise RuntimeError(f'Execution did not complete successfully: {status}')
        if time.monotonic() >= deadline:
            cancelled = request('POST', status_path + '/cancel')
            if cancelled.get('success') is not True:
                raise RuntimeError(f'Timed out; cancellation not confirmed for {execution_id}')
            raise TimeoutError(f'Execution timed out and was cancelled: {execution_id}')
        time.sleep(poll_interval)
    report = request('GET', status_path + '/report')
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if report.get('execution_id') != execution_id or report.get('status') != 'completed':
        raise RuntimeError('Report does not match the completed execution')
    count = report.get('summary', {}).get('total_requests')
    if not isinstance(count, (int, float)) or not math.isfinite(count) or count <= 0:
        raise RuntimeError('Report contains no measured requests')
    artifact = request('GET', f'/reports/{execution_id}/result.jtl')
    if not isinstance(artifact, bytes) or not artifact.strip():
        raise RuntimeError('JTL artifact is missing or empty')
    (output / 'result.jtl').write_bytes(artifact)
    sla = report.get('sla_result') or {}
    if sla.get('verdict') != 'pass' or not sla.get('total_checks'):
        raise RuntimeError(f"SLA gate did not pass: {sla.get('verdict', 'missing')}")
    if baseline_id:
        query = urlencode({'baseline_execution_id': identifier(baseline_id), 'target_execution_id': execution_id})
        comparison = request('POST', '/api/v1/analysis/compare-versions?' + query)
        (output / 'comparison.json').write_text(json.dumps(comparison, indent=2), encoding='utf-8')
        if comparison.get('overall_verdict') not in {'stable', 'improved'}:
            raise RuntimeError('Baseline comparison did not pass')
    return execution_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='performance-artifacts')
    parser.add_argument('--mode', choices=['run', 'drift'], default='run')
    parser.add_argument('--timeout', type=float, default=3300)
    args = parser.parse_args(argv)
    try:
        if args.timeout <= 0 or not math.isfinite(args.timeout):
            raise ValueError('Timeout must be positive and finite')
        request = APIClient(os.environ.get('AGENT_URL', ''), os.environ.get('AGENT_TOKEN', ''))
        if args.mode == 'drift':
            project = os.environ.get('AGENT_PROJECT_ID')
            if not project:
                raise ValueError('AGENT_PROJECT_ID is required for drift mode')
            result = request('POST', '/api/v1/analysis/drift?' + urlencode({'project_id': project, 'days': 30}))
            output = Path(args.output)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'drift.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
            if result.get('health') != 'stable':
                raise RuntimeError(f"Drift gate did not pass: {result.get('health', 'missing')}")
        else:
            execution_id = run_gate(request, os.environ.get('AGENT_PLAN_ID'), args.output,
                timeout=args.timeout, baseline_id=os.environ.get('AGENT_BASELINE_EXECUTION_ID'))
            print(f'Performance gate passed: {execution_id}')
        return 0
    except Exception as exc:
        print(f'Performance gate failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

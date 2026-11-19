"""Actual loopback HTTP checks; no file upload, provider calls or secret output."""
import argparse
import json
import time
import urllib.error
import urllib.request

from safety import PURPOSE, load, write_json


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def smoke(run_id):
    folder, state = load(run_id)
    assert state['backend'] == 'docker' and not state.get('identity_only', True)
    port = state['api_port']
    assert type(port) is int and 1024 < port < 65536
    base = f'http://127.0.0.1:{port}'
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(path, *, payload=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(base + path, data=data, headers=headers)
        try:
            response = client.open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            return response.code, json.loads(raw) if raw else {}, response.headers

    deadline = time.monotonic() + 30
    while True:
        try:
            code, status, _ = request('/poc/status')
            assert code == 200 and status == {
                'run_id': run_id, 'purpose': PURPOSE,
                'documents_enabled': False, 'llm_enabled': False}
            break
        except (OSError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise RuntimeError('isolated_api_not_ready') from None
            time.sleep(1)
    assert request('/auth/me')[0] == 401
    assert request('/auth/me', token='forged')[0] == 401
    secrets = json.loads((folder / 'secrets.json').read_text())
    mapping = json.loads((folder / 'mapping.json').read_text())
    tokens = {}
    for actor in mapping['users']:
        code, value, headers = request('/auth/dev/login', payload={
            'email': f'poc2-{actor}@example.invalid',
            'password': secrets['accounts'][actor]})
        assert code == 200 and headers['Cache-Control'] == 'no-store'
        tokens[actor] = value['access_token']
        assert request('/auth/me', token=tokens[actor])[0] == 200
    primary = state['tenants']['primary']['id']
    other = state['tenants']['other']['id']
    matrix = [('employee', primary, 'read', 200), ('employee', primary, 'upload', 403),
              ('head', primary, 'upload', 200), ('deputy', primary, 'upload', 200),
              ('other', primary, 'read', 403), ('employee', other, 'read', 403),
              ('denied', primary, 'read', 403), ('other', other, 'read', 200)]
    for actor, department, permission, expected in matrix:
        assert request(f'/poc/permissions/{department}/documents:{permission}',
                       token=tokens[actor])[0] == expected
    for path in ('/api/documents/ask', '/api/documents/upload'):
        assert request(path, payload={}, token=tokens['head'])[0] == 503
    for token in tokens.values():
        assert request('/auth/logout', payload={}, token=token)[0] == 204
        assert request('/auth/me', token=token)[0] == 401
    write_json(folder / 'http-verification.json', {'run_id': run_id,
        'actual_loopback_http_passed': True, 'authenticated_users': len(tokens),
        'permission_cases': len(matrix), 'all_test_sessions_revoked': True,
        'documents_processed': 0, 'model_requests': 0})
    print('Loopback HTTP verification passed: six users, eight permission cases; sessions revoked.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    try:
        smoke(args.run)
    except Exception:
        raise SystemExit('Phase 2A HTTP verification failed; no credentials printed.') from None

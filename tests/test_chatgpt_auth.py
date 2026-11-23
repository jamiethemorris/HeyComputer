"""Offline SIWC tests: only a disposable IPv4 loopback listener is real."""
import base64
import hashlib
import http.client
import json
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit

import jwt
from jwt.algorithms import RSAAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa

from heycomputer import chatgpt_auth as auth


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = dict(json.loads(RSAAlgorithm.to_jwk(cls.key.public_key())), kid='test')

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.file = self.root / auth.FILE
        self.pool = self.enterContext(ThreadPoolExecutor(1))
        self.callback = {'client_id': 'oaiapp_fixture', 'code': 'fixture-code'}
        self.fields = {}
        self.invalid_visits = []
        self.claims = {}
        self.enterContext(patch.object(auth, '_open_browser', side_effect=self.browser))
        self.request = self.enterContext(patch.object(auth, '_request', side_effect=self.issuer))

    def browser(self, url):
        u = urlsplit(url)
        self.assertEqual(u._replace(query='').geturl(), 'https://auth.openai.com/api/accounts/authorize')
        self.query = dict(parse_qsl(u.query))
        redirect = urlsplit(self.query['redirect_uri'])
        self.assertEqual((redirect.scheme, redirect.hostname, redirect.path), ('http', '127.0.0.1', '/auth/callback'))
        def get(path, query, headers):
            conn = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=3)
            try:
                conn.request('GET', path + '?' + query, headers=headers)
                r = conn.getresponse()
                r.read()
                return r.status
            finally:
                conn.close()
        def visit():
            params = dict({'state': self.query['state']}, **self.callback)
            for path, state, suffix, headers in self.invalid_visits:
                query = urlencode(dict(params, state=state or params['state'])) + suffix
                self.assertEqual(get(path, query, headers), 400)
            return get(redirect.path, urlencode(params), {})
        self.visit = self.pool.submit(visit)

    def issuer(self, url, form=None, **kwargs):
        if url == auth.JWKS:
            return {'keys': [self.jwk]}
        self.assertEqual(url, 'https://auth.openai.com/api/accounts/oauth/token')
        assert form is not None
        verifier = form['code_verifier'].encode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b'=').decode()
        self.assertEqual(challenge, self.query['code_challenge'])
        self.assertEqual(self.query['code_challenge_method'], 'S256')
        self.assertEqual(form, dict(client_id='oaiapp_fixture', code='fixture-code', code_verifier=verifier.decode(),
                                    redirect_uri=self.query['redirect_uri'], resource=auth.RESOURCE,
                                    grant_type='authorization_code'))
        claims = dict(iss=auth.ISSUER, aud=form['client_id'], sub='fixture-sub', iat=int(time.time()),
                      exp=int(time.time()) + 600, nonce=self.query['nonce'])
        claims.update(self.claims)
        identity = jwt.encode(claims, self.key, algorithm='RS256', headers={'kid': 'test'})
        return dict(dict(access_token='fake-access', refresh_token='fake-refresh', id_token=identity,
                         token_type='Bearer', scope=auth.SCOPE, expires_in=3600), **self.fields)

    def test_empty_status_is_read_only(self):
        self.assertEqual(auth.account_status(self.root), 'Signed out')
        self.assertEqual(list(self.root.iterdir()), [])
        with self.assertRaisesRegex(auth.PlanError, 'Sign in'):
            auth.access_token(self.root)
        self.assertEqual(auth.sign_out(self.root), 'Signed out')
        self.request.assert_not_called()

    def test_sign_in_reauth_and_sign_out(self):
        self.assertEqual(auth.sign_in(self.root), 'Signed in')
        self.assertEqual(self.visit.result(), 200)
        first = self.query.copy()
        self.assertEqual(first['client_id'], 'dynamic_agent_client')
        self.assertEqual(first['agent_name_hint'], 'HeyComputer')
        self.assertEqual(first['scope'], 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct')
        self.assertEqual(first['resource'], 'https://api.openai.com/v1')
        self.assertTrue(first['ext_agent_host_id'].startswith('urn:uuid:'))
        self.assertEqual(stat.S_IMODE(self.file.stat().st_mode), 0o600)
        self.assertEqual(auth.access_token(self.root), 'fake-access')
        del self.callback['client_id']
        auth.sign_in(self.root)
        self.assertEqual(self.query['client_id'], 'oaiapp_fixture')
        self.assertTrue(self.query['id_token_hint'])
        self.assertNotIn('agent_name_hint', self.query)
        for field in ('state', 'nonce', 'code_challenge'):
            self.assertNotEqual(first[field], self.query[field])
        revoke = auth.ISSUER + '/fixture-revoke'
        def revoke_request(url, form=None, **kwargs):
            if form is None:
                self.assertEqual(url, auth.ISSUER + '/.well-known/openid-configuration')
                return {'issuer': auth.ISSUER, 'revocation_endpoint': revoke}
            self.assertEqual(url, revoke)
            self.assertEqual(form, dict(client_id='oaiapp_fixture', token_type_hint='refresh_token', token='fake-refresh'))
        self.request.side_effect = revoke_request
        self.assertEqual(auth.sign_out(self.root), 'Signed out')
        self.assertEqual(set(json.loads(self.file.read_text())), {'client_id', 'sub', 'ext_agent_host_id'})
        self.request.side_effect = self.issuer
        auth.sign_in(self.root)
        self.assertNotIn('id_token_hint', self.query)
        self.assertEqual(self.query['client_id'], 'oaiapp_fixture')
        self.assertEqual(self.query['ext_agent_host_id'], first['ext_agent_host_id'])

    def test_invalid_identity_does_not_replace_session(self):
        auth.sign_in(self.root)
        saved = self.file.read_bytes()
        for changes in ({'nonce':'bad'}, {'aud':'other'}, {'iss':'other'}, {'exp':1}, {'sub':'different'}):
            with self.subTest(field=next(iter(changes))), self.assertRaises(auth.PlanError):
                self.claims = changes
                auth.sign_in(self.root)
            self.assertEqual(self.file.read_bytes(), saved)
        self.claims = {}
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with patch.object(self, 'key', other), self.assertRaises(auth.PlanError):
            auth.sign_in(self.root)
        self.assertEqual(self.file.read_bytes(), saved)

    def test_spurious_callbacks_do_not_consume_attempt(self):
        self.invalid_visits = [('/other', '', '', {}), ('/auth/callback', '', '', {'Host': 'other.invalid'}),
                               ('/auth/callback', 'wrong', '', {}), ('/auth/callback', '', '&state=bad', {})]
        self.assertEqual(auth.sign_in(self.root), 'Signed in')
        self.assertEqual(self.visit.result(), 200)

    def test_incomplete_denied_and_mismatched_registration(self):
        for self.callback in ({'code':'fixture'}, {'code':'fixture', 'client_id':auth.DYNAMIC}, {'error':'access_denied'}):
            with self.assertRaises(auth.PlanError):
                auth.sign_in(self.root)
            self.request.assert_not_called()
        self.callback = {'client_id':'oaiapp_fixture', 'code':'fixture-code'}
        auth.sign_in(self.root)
        self.request.reset_mock()
        self.callback['client_id'] = 'other'
        with self.assertRaises(auth.PlanError):
            auth.sign_in(self.root)
        self.request.assert_not_called()

    def test_no_direct_grant_never_yields_token(self):
        self.fields['scope'] = 'openid profile'
        self.callback['scope'] = auth.SCOPE
        self.assertEqual(auth.sign_in(self.root), auth.DISABLED)
        with self.assertRaises(auth.PlanError):
            auth.access_token(self.root)
        auth.sign_in(self.root)
        self.assertEqual(self.query['prompt'], 'consent')

    def test_unsafe_files_are_rejected(self):
        self.file.symlink_to(self.root / 'missing')
        with self.assertRaises(auth.PlanError):
            auth.sign_in(self.root)
        self.file.unlink()
        self.file.write_text('{}')
        self.file.chmod(0o644)
        with self.assertRaises(auth.PlanError):
            auth.account_status(self.root)

    def test_foreign_revocation_endpoint_is_not_sent_a_token(self):
        auth.sign_in(self.root)
        self.request.reset_mock()
        self.request.side_effect = None
        self.request.return_value = {'issuer': auth.ISSUER, 'revocation_endpoint': 'https://foreign.invalid/revoke'}
        self.assertEqual(auth.sign_out(self.root), auth.UNCONFIRMED)
        self.request.assert_called_once_with(auth.ISSUER + '/.well-known/openid-configuration')
        self.assertEqual(auth.account_status(self.root), 'Signed out')
        self.assertNotIn('session', json.loads(self.file.read_text()))

    def test_unconfirmed_revocation_retries_then_clears(self):
        auth.sign_in(self.root)
        self.request.reset_mock()
        self.request.side_effect = auth._Unavailable('offline')
        with patch.object(auth.time, 'sleep') as backoff:
            self.assertEqual(auth.sign_out(self.root), auth.UNCONFIRMED)
        backoff.assert_called_once()
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(auth.account_status(self.root), 'Signed out')

    def test_refresh_error_recovery(self):
        self.fields['expires_in'] = 1
        auth.sign_in(self.root)
        saved = self.file.read_bytes()
        self.request.side_effect = auth._Unavailable('offline')
        with self.assertRaises(auth.PlanError):
            auth.access_token(self.root)
        self.assertEqual(self.file.read_bytes(), saved)
        self.request.side_effect = auth._UnusableGrant('invalid_grant')
        with self.assertRaisesRegex(auth.PlanError, 'Sign in again'):
            auth.access_token(self.root)
        self.assertEqual(auth.account_status(self.root), 'Signed out')
        self.assertEqual(json.loads(self.file.read_text())['client_id'], 'oaiapp_fixture')

    def test_refresh_requires_a_replacement(self):
        self.fields['expires_in'] = 1
        auth.sign_in(self.root)
        self.request.side_effect = None
        self.request.return_value = dict(access_token='next', token_type='Bearer', scope=auth.SCOPE, expires_in=3600)
        with self.assertRaises(auth.PlanError):
            auth.access_token(self.root)
        self.assertNotIn('next', self.file.read_text())

    def test_corrupt_expiry_fails_closed(self):
        auth.sign_in(self.root)
        saved = json.loads(self.file.read_text())
        saved['session']['expires_at'] = float('nan')
        self.file.write_text(json.dumps(saved))
        with self.assertRaises(auth.PlanError):
            auth.access_token(self.root)

    def test_failed_replacement_keeps_private_old_file(self):
        auth.sign_in(self.root)
        saved = self.file.read_bytes()
        with patch.object(auth.os, 'replace', side_effect=OSError), self.assertRaises(auth.PlanError):
            auth.sign_in(self.root)
        self.assertEqual(self.file.read_bytes(), saved)
        self.assertEqual(list(self.root.iterdir()), [self.file])

    def test_invalid_grant_restarts_using_issued_id(self):
        attempts = []
        def issuer(url, form=None):
            if url == auth.TOKEN:
                attempts.append(self.query.copy())
                if len(attempts) == 1:
                    del self.callback['client_id']
                    raise auth._UnusableGrant('invalid_grant')
            return self.issuer(url, form)
        self.request.side_effect = issuer
        self.assertEqual(auth.sign_in(self.root), 'Signed in')
        self.assertEqual([q['client_id'] for q in attempts], [auth.DYNAMIC, 'oaiapp_fixture'])
        self.assertNotEqual(attempts[0]['code_challenge'], attempts[1]['code_challenge'])

    def test_concurrent_processes_rotate_only_once(self):
        self.fields['expires_in'] = 1
        auth.sign_in(self.root)
        script = '''
import sys,time
from pathlib import Path
from heycomputer import chatgpt_auth as a
root = Path(sys.argv[1])
def rotate(url, form):
    assert url == a.TOKEN
    assert form == dict(resource=a.RESOURCE, client_id='oaiapp_fixture', grant_type='refresh_token', refresh_token='fake-refresh')
    (root/'once').touch(exist_ok=False)
    time.sleep(.2)
    return dict(access_token='next', refresh_token='replacement', scope=a.SCOPE, expires_in=3600, token_type='Bearer')
a._request = rotate
assert a.access_token(root) == 'next'
'''
        args = [sys.executable, '-B', '-c', script, str(self.root)]
        with subprocess.Popen(args) as one, subprocess.Popen(args) as two:
            self.assertEqual([one.wait(10), two.wait(10)], [0, 0])
        self.assertNotIn('fake-refresh', self.file.read_text())
        self.assertIn('replacement', self.file.read_text())


class IssuerTransportTests(unittest.TestCase):
    @patch.object(http.client, 'HTTPSConnection')
    def test_never_follows_redirect_or_reports_response_body(self, connect):
        conn = connect.return_value
        response = conn.getresponse.return_value
        response.status = 307
        response.read.return_value = b'{"error":"private fixture detail"}'
        with self.assertRaises(auth.PlanError) as caught:
            auth._request(auth.TOKEN, {'code': 'fixture'})
        self.assertNotIn('fixture', str(caught.exception))
        connect.assert_called_once_with('auth.openai.com', timeout=12)
        conn.request.assert_called_once_with('POST', '/api/accounts/oauth/token', body='code=fixture',
                                             headers={'Content-Type': 'application/x-www-form-urlencoded'})

    @patch.object(http.client, 'HTTPSConnection')
    def test_revocation_requires_empty_success(self, connect):
        response = connect.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{}'
        with self.assertRaises(auth.PlanError):
            auth._request(auth.ISSUER + '/revoke', {}, empty=True)
        response.read.return_value = b''
        self.assertEqual(auth._request(auth.ISSUER + '/revoke', {}, empty=True), {})

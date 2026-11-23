"""One local ChatGPT registration per HeyComputer data root (POSIX)."""
import base64
import fcntl
import hashlib
import http.client
import json
import os
import re
import secrets
import stat
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt

from .protocol import PlanError

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
JWKS = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
DIRECT = "chatgpt.tokens.use.direct"
SCOPE = "openid profile email offline_access resource.invoke " + DIRECT
DYNAMIC = "dynamic_agent_client"
FILE = "chatgpt-auth.json"
LIMIT = 256 * 1024
SIGNED_OUT = "Signed out"
DISABLED = "Signed in (ChatGPT plan usage not enabled)"
UNCONFIRMED = "Signed out locally; remote revocation was not confirmed. Disconnect the app in ChatGPT Settings."


@contextmanager
def _store(root, *, write):
    # Lock the stable directory inode, never the atomically replaced file.
    try:
        if write:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            if write:
                raise
            yield {}
            return
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise PlanError("ChatGPT data directory is not private to its owner.")
            if write:
                fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                record = os.open(FILE, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                data = {}
            else:
                with os.fdopen(record, 'rb') as f:
                    info = os.fstat(f.fileno())
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or info.st_mode & 0o077 or info.st_size > LIMIT):
                        raise PlanError("Unsafe ChatGPT credential file.")
                    data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError
            yield data
        finally:
            os.close(fd)
    except PlanError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise PlanError("Could not use the local ChatGPT account record.") from None


def _save(root, data):
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".chatgpt-")  # 0600 from creation
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, root / FILE)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _status(data):
    if not data.get("session"):
        return SIGNED_OUT
    return "Signed in" if DIRECT in data["session"]["scope"].split() else DISABLED


def account_status(root: Path) -> str:
    """Local status only; does not refresh, contact OpenAI, or expose identifiers."""
    with _store(root, write=False) as data:
        return _status(data)


def sign_out(root: Path) -> str:
    """Forget all local tokens; distinguish confirmed from unconfirmed revocation."""
    with _store(root, write=True) as data:
        session = data.pop("session", None)
        if not session:
            return SIGNED_OUT
        try:
            if session['refresh_token']:
                _revoke(data['client_id'], session['refresh_token'])
            return SIGNED_OUT
        except PlanError:
            return UNCONFIRMED
        finally:
            _save(root, data)


def access_token(root: Path) -> str:
    """Return a bearer token to the in-process OpenAI caller, never the panel."""
    with _store(root, write=True) as data:
        if _status(data) == SIGNED_OUT:
            raise PlanError("Sign in with ChatGPT first.")
        session = data['session']
        if _status(data) == DISABLED:
            raise PlanError(DISABLED)
        if type(session['expires_at']) is not int or not isinstance(session['access_token'], str):
            raise PlanError('Invalid local ChatGPT session. Sign in again.')
        if session['expires_at'] <= time.time() + 60:
            try:
                reply = _request(TOKEN, dict(grant_type='refresh_token', client_id=data['client_id'],
                                             refresh_token=session['refresh_token'], resource=RESOURCE))
            except _UnusableGrant:
                del data['session']
                _save(root, data)
                raise PlanError('ChatGPT session expired. Sign in again.') from None
            # Keep only the last *validated* ID token as a future login hint.
            session.update(_tokens(reply))
            _save(root, data)
            if _status(data) == DISABLED:
                raise PlanError(DISABLED)
        return session['access_token']


class _UnusableGrant(PlanError):
    pass


class _Unavailable(PlanError):
    pass


_TERMINAL = {'invalid_grant', 'invalid_refresh_token', 'token_expired', 'refresh_token_expired',
             'refresh_token_invalidated', 'refresh_token_reused'}


def _issuer_path(url):
    if not isinstance(url, str) or not re.fullmatch(r'https://auth\.openai\.com/[A-Za-z0-9._~/-]+', url):
        raise PlanError('Invalid OpenAI issuer endpoint.')
    return urlsplit(url).path


def _request(url, form=None, *, empty=False):
    # HTTPSConnection neither follows redirects nor consults proxy environment variables.
    path = _issuer_path(url)
    try:
        with closing(http.client.HTTPSConnection('auth.openai.com', timeout=12)) as conn:
            conn.set_debuglevel(0)
            body = urlencode(form) if form is not None else None
            headers = {'Content-Type': 'application/x-www-form-urlencoded'} if body is not None else {}
            conn.request('POST' if body is not None else 'GET', path, body=body, headers=headers)
            response = conn.getresponse()
            if response.status >= 500:
                raise _Unavailable('OpenAI sign-in service unavailable. Try again later.')
            raw = response.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError
            if response.status == 200 and empty and not raw:
                return {}
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError
            if response.status in (400, 401):
                error = obj.get('error')
                code = error.get('code') if isinstance(error, dict) else error
                if isinstance(code, str) and code in _TERMINAL:
                    raise _UnusableGrant('OpenAI authorization is no longer valid.')
            if response.status != 200 or empty:
                raise ValueError
            return obj
    except PlanError:
        raise
    except (OSError, http.client.HTTPException):
        raise _Unavailable('Cannot reach OpenAI sign-in service. Try again later.') from None
    except (ValueError, RecursionError):
        raise PlanError('OpenAI returned an invalid or unsuccessful authorization response.') from None


def _revoke(client, token):
    for attempt in range(2):
        try:
            doc = _request(ISSUER + '/.well-known/openid-configuration')
            if doc.get('issuer') != ISSUER:
                raise PlanError('Unexpected discovery issuer.')
            endpoint = doc.get('revocation_endpoint')
            _issuer_path(endpoint)  # Validate before putting the refresh token into a request.
            _request(endpoint, dict(client_id=client, token_type_hint='refresh_token', token=token), empty=True)
            return
        except _Unavailable:
            if attempt:
                raise
            time.sleep(0.25)


def _tokens(reply):
    scope = reply.get('scope', '')
    if not isinstance(scope, str):
        raise PlanError('Invalid ChatGPT grant.')
    enabled = DIRECT in scope.split()
    access = reply.get('access_token', '')
    refresh = reply.get('refresh_token', '')
    ttl = reply.get('expires_in', 0)
    if (not isinstance(access, str) or not isinstance(refresh, str) or type(ttl) is not int
            or ttl < 0 or ttl > 2**53 or (enabled and (not access or not refresh or ttl == 0
            or str(reply.get('token_type')).lower() != 'bearer'))
            or any(ord(c) < 33 or ord(c) > 126 for c in access + refresh)):
        raise PlanError('Invalid ChatGPT token response.')
    return dict(access_token=access, refresh_token=refresh, scope=scope, expires_at=int(time.time()) + ttl)


def _subject(identity, client, nonce):
    try:
        header = jwt.get_unverified_header(identity)
        kid = header.get('kid')
        if header.get('alg') != 'RS256' or not isinstance(kid, str) or not kid:
            raise ValueError
        keys = _request(JWKS)['keys']
        key, = [jwk for jwk in keys if isinstance(jwk, dict) and jwk.get('kid') == kid
                and jwk.get('kty') == 'RSA' and jwk.get('use', 'sig') == 'sig'
                and jwk.get('alg', 'RS256') == 'RS256']
        claims = jwt.decode(identity, jwt.PyJWK.from_dict(key).key, algorithms=['RS256'],
                            issuer=ISSUER, audience=client,
                            options={'require': ['exp', 'iat', 'sub', 'nonce']})
        if (claims['nonce'] != nonce or not isinstance(claims['sub'], str) or not claims['sub']
                or claims.get('azp', client) != client
                or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and 'azp' not in claims)):
            raise ValueError
        return claims['sub']
    except PlanError:
        raise
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, OverflowError):
        raise PlanError('ChatGPT identity verification failed.') from None


def _open_browser(url):
    # Native macOS browser handoff: no bearer credentials or ID hint in shell/argv.
    try:
        from AppKit import NSWorkspace, NSURL
        if NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url)):
            return
    except Exception:
        pass  # Native errors may include the URL.
    raise PlanError('Could not open the browser for ChatGPT sign-in.')


def _authorize(data, client, nonce, verifier):
    state = secrets.token_urlsafe(32)
    callback = {}

    class Handler(BaseHTTPRequestHandler):
        timeout = 2

        def log_message(self, format, *args):
            pass  # The default logs callback query strings.

        def do_GET(self):
            try:
                path, _, query = self.path.partition('?')
                if path != '/auth/callback' or self.headers.get_all('Host') != [host]:
                    raise ValueError
                pairs = parse_qs(query, keep_blank_values=True, max_num_fields=16)
                if any(len(v) != 1 for v in pairs.values()) or not secrets.compare_digest(pairs['state'][0], state):
                    raise ValueError
                callback.update({k: v[0] for k, v in pairs.items()})
                self.send_response(200)
            except (ValueError, KeyError, TypeError):
                self.send_response(400)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Return to HeyComputer to check sign-in status.')

    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        server.handle_error = lambda request, client_address: None  # No browser socket tracebacks.
        host = f'127.0.0.1:{server.server_port}'
        redirect = f'http://{host}/auth/callback'
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        params = dict(client_id=client, ext_agent_host_id=data['ext_agent_host_id'], response_type='code',
                      redirect_uri=redirect, scope=SCOPE, resource=RESOURCE, state=state, nonce=nonce,
                      code_challenge_method='S256', code_challenge=challenge)
        if client == DYNAMIC:
            params['agent_name_hint'] = 'HeyComputer'
        elif data.get('session'):
            params['id_token_hint'] = data['session']['id_token']
            if _status(data) == DISABLED:
                params['prompt'] = 'consent'
        _open_browser(AUTHORIZE + '?' + urlencode(params))
        deadline = time.monotonic() + 180
        while not callback:
            server.timeout = deadline - time.monotonic()
            if server.timeout <= 0:
                raise PlanError('ChatGPT sign-in timed out.')
            server.handle_request()
    if 'error' in callback or not callback.get('code'):
        raise PlanError('ChatGPT authorization was not completed.')
    issued = callback.get('client_id', client)
    if not issued or issued == DYNAMIC or (client != DYNAMIC and issued != client):
        raise PlanError('Unexpected or missing ChatGPT registration.')
    return issued, dict(grant_type='authorization_code', client_id=issued, code=callback['code'],
                        code_verifier=verifier, redirect_uri=redirect, resource=RESOURCE)


def sign_in(root: Path) -> str:
    """Authorize the local registration; return the same public status as account_status."""
    with _store(root, write=True) as data:
        if 'ext_agent_host_id' not in data:
            data['ext_agent_host_id'] = uuid.uuid4().urn
            _save(root, data)
        client = data.get('client_id', DYNAMIC)
        for attempt in range(2):
            nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
            client, form = _authorize(data, client, nonce, verifier)
            try:
                reply = _request(TOKEN, form)
                break
            except _UnusableGrant:
                if attempt:
                    raise
        identity = reply.get('id_token')
        sub = _subject(identity, client, nonce)
        if data.get('sub', sub) != sub:
            raise PlanError('ChatGPT account does not match the selected registration.')
        data.update(sub=sub, client_id=client, session=dict(_tokens(reply), id_token=identity))
        _save(root, data)
        return _status(data)

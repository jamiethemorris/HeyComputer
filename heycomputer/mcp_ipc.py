"""One outstanding panel call in the worker's private session directory."""
import time
from uuid import uuid4

from .mcp_catalog import decode, require
from .protocol import PlanError


def exchange(directory, job, project, request, check, seconds, publish):
    # Only the validated worker job ID enters a path, never a model call ID.
    call = directory / f'{job}.companion-call.json'
    receipt = directory / f'{job}.companion-reply.json'
    binding = dict(session=directory.name, job=job, project=project, ticket=uuid4().hex)
    expires = int(time.time() + seconds)  # Lua os.time() has whole-second resolution.
    def live():
        check()  # Includes original monotonic job deadline, cancellation and project guard.
        require(time.time() < expires, 'Native call expired. Check REAPER before retrying.')

    live()
    require(not call.exists() and not receipt.exists(), 'Unexpected outstanding native call.')
    try:
        publish(call, dict(binding, expires=expires, request=request))
        while True:
            live()
            try:
                with receipt.open('rb') as stream:
                    raw = stream.read(65537)
            except FileNotFoundError:
                time.sleep(.05)
                continue  # Poll for the receipt; never republish the call.
            require(len(raw) <= 65536, 'Oversized native receipt.')
            envelope = decode(raw.decode('utf-8'), 65536)
            require(isinstance(envelope, dict) and all(envelope.get(k) == v for k,v in binding.items()),
                    'Mismatched native receipt.')
            reply = envelope.get('receipt')
            if not isinstance(reply, dict) or reply.get('id') != request['id']:
                raise PlanError('Mismatched native call ID.')
            if reply.get('status') == 'ok':
                require(set(reply) == {'id','status','data'} and isinstance(reply['data'], dict), 'Invalid native receipt.')
            else:
                require(reply.get('status') in ('refused', 'unknown') and set(reply) == {'id','status','error'}
                        and isinstance(reply['error'], str) and reply['error'], 'Invalid native failure receipt.')
            if request['name'] == 'hc_tracks' and reply['status'] == 'ok':
                require(reply['data'].get('project') == project, 'Mismatched native inventory.')
            live()
            return reply
    finally:
        call.unlink(missing_ok=True)
        receipt.unlink(missing_ok=True)

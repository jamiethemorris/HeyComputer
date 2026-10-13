"""Validate host-inspected plugin parameter identities; never infer knob scales."""
import json
from .protocol import PlanError


def validate_parameter(action, snapshot):
    try:
        value = json.loads(action['value'])
    except (ValueError, TypeError):
        raise PlanError('Invalid plugin parameter setting.') from None
    if (not isinstance(value, dict) or set(value) != {'fx', 'index', 'name', 'value', 'exact'}
            or type(value['index']) is not int or type(value['exact']) is not bool
            or not all(isinstance(value[k], str) for k in ('fx', 'name', 'value'))
            or not 1 <= len(value['value']) <= 100):
        raise PlanError('Invalid plugin parameter setting.')
    for catalog in snapshot.get('fx_parameters') or []:
        if catalog.get('track') == action['target'] and catalog.get('guid') == value['fx']:
            for param in catalog.get('parameters', []):
                if param.get('index') == value['index'] and param.get('name') == value['name'] and param.get('direct') is True:
                    return
    raise PlanError('Inspect this exact plugin parameter before changing it directly.')


HELP = """
Before any third-party plugin parameter task, inspect the exact plugin's host-exposed controls. Return kind=inspect, actions containing native setup if needed followed by inspect_fx as the LAST operation on exactly one track. inspect_fx value is last_added or an exact fx_instances GUID (same targeting rules as open_fx). message contains ONLY the remaining task, preserving all parameter values, explicit precision requirements, and whether to open the plugin afterwards; lua empty. Inspection is local and does not open the GUI. Never repeat completed setup. For new plugin tasks use create_tracks/add_fx/inspect_fx, not visual preparation. Reuse an existing instance only when requested. One inspection per task; if project.fx_parameters is already present, use that evidence and do not inspect again.
With project.fx_parameters, prefer set_fx_param for static settings marked direct=true, or automation_points for a time-varying envelope request. Do not substitute static knob changes for requested automation. target MUST be the catalog's exact track GUID. value is JSON with exactly {"fx":"exact catalog GUID","index":0,"name":"exact inspected parameter name","value":"300 Hz","exact":false}. index and name MUST match the catalog; never guess parameter indices, normalized values, scale formulas or labels. value is a displayed numeric value WITH units (Hz/kHz, dB, ms/s, %, or no unit only when the plugin reports none), or an exact discrete choice label from choices. samples show the plugin's formatted values at normalized 0, 0.5 and 1, NOT a linear scale. Native code translates and verifies values using the plugin's formatter. exact=true only when user explicitly requests exact precision; default false. If formatted samples have no units, express the value in that reported unit convention only if unambiguous. Numeric prefixes such as 'C3' or compound ratios are not supported. Parameter queries can return reply using the inspected values. The catalog may be truncated. Missing/direct=false controls, unclear scales or non-exposed operations need visual fallback or a focused clarification; never invent an adapter.
For EQ band creation, inspect the reported band Used/Enabled controls and choose an unused band, preserve other bands, set shape/frequency/gain and enable/use that band as appropriate. Do not activate an already used band as if new. If host parameters do not establish this safely, use visual fallback. If user also requested opening the plugin, append open_fx using its catalog GUID after parameter edits. Offline plugins need an explicit request to bring them online using offline_fx on=false; include that operation before inspect_fx when the user requests online plus parameter edits. Never infer an online change from a parameter request alone. Pure copy/move/bypass/offline/reorder/delete operations use native chain management and need no parameter inspection. A plan may change multiple inspected parameters in one native batch. Preserve all remaining requested operations when falling back to vision.
"""

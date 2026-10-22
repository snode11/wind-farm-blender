"""Lossless diagnostic reports and actionable, conservative recovery hints."""
import json


def recovery_hint(component='', status='', detail=''):
    text = str(detail).lower()
    if component == 'blender' and status == 'UNSUPPORTED':
        return 'Use Blender 5.2 or newer, then run Check Environment again.'
    if component == 'python' and status == 'UNSUPPORTED':
        return 'Select a Python 3.11+ executable in Backend Python, then check again.'
    if status in {'MISSING', 'INVALID'}:
        return 'Configure the absolute path to an existing executable and check its permissions. Local Demo does not require backend tools.'
    if status == 'TIMEOUT' or 'timed out' in text or 'timeout' in text:
        return 'Check whether the backend or executable responds; inspect its logs before retrying the check or connection.'
    if 'refused' in text:
        return 'Check that the backend is running and its port matches the configured port, then reconnect.'
    if 'permission' in text:
        return 'Check read/write or executable permissions for the path reported below, then retry.'
    if 'no such file' in text or 'not found' in text:
        return 'Check the reported path and configured project/environment paths, then retry.'
    if component == 'fastfarm' and status == 'CHECKED':
        return 'Only executable access was checked; verify simulator capability through the backend workflow.'
    if status in {'READY', 'CANCELLED'}:
        return ''
    if component == 'training':
        return 'Inspect the trainer log and metric source below; check that the trainer is still producing records.'
    return 'Review the full details and backend logs. Copy this report when requesting help; it may contain local paths.'


def report(title, detail, *, component='', status='', provenance=None):
    parts = [title, str(detail)]
    if provenance is not None:
        parts.extend(['Source', json.dumps(provenance, ensure_ascii=False, indent=2, default=str)])
    hint = recovery_hint(component, status, detail)
    if hint:
        parts.extend(['Suggested action', hint])
    return '\n\n'.join(parts)

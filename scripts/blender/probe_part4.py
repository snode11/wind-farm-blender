"""Exercise the new Bridge lifecycle without launching Blender or old tests.

Start a Bridge first, then run this script with the same scene/options as the UI.
The JSON report contains the actual received wire payloads for numerical review.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
from wfrl_blender.transport import TransportClient


def probe(port, options, timeout=120.0, mode='interactive_training'):
    client = TransportClient(port=port)
    report = {'port': port, 'mode': mode, 'options': options, 'messages': [], 'passed': False}
    status = None
    latest = None
    latest_sequence = None
    paused_sequence = None

    def poll_until(predicate, description):
        nonlocal status, latest, latest_sequence, paused_sequence
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in client.poll():
                report['messages'].append(message)
                if message['type'] in ('hello_ack', 'lifecycle'):
                    status = message['payload']['run_status']
                    if message['type'] == 'lifecycle' and status == 'PAUSED':
                        paused_sequence = message['sequence']
                elif message['type'] == 'snapshot':
                    latest = message['payload']
                    latest_sequence = message['sequence']
                elif message['type'] == 'error':
                    raise RuntimeError(message['payload']['message'])
            if client.last_error:
                raise RuntimeError(client.last_error)
            if status == 'FAILED':
                raise RuntimeError('Backend entered FAILED')
            if predicate():
                return
            time.sleep(0.01)
        raise TimeoutError(description)

    def command(name, arguments=None):
        client.send(dict(protocol_version=1, type='command',
                         session_id=client.session_id, sequence=client.next_sequence,
                         payload=dict(command=name, arguments=arguments or {})))

    try:
        client.connect()
        poll_until(lambda: client.status == 'CONNECTED', 'handshake')
        command('run.start', {'mode': mode, 'options': options})
        poll_until(lambda: status == 'RUNNING' and latest is not None, 'first snapshot')
        paused_sequence = None
        command('run.pause')
        poll_until(lambda: (status == 'PAUSED' and paused_sequence is not None and
                            latest_sequence is not None and latest_sequence > paused_sequence),
                   'pause at control boundary')
        before = latest['step']
        command('run.step')
        poll_until(lambda: latest['step'] > before and status == 'PAUSED', 'single step')
        if latest['step'] != before + 1:
            raise AssertionError(f"Single step advanced {before} -> {latest['step']}")
        command('run.resume')
        poll_until(lambda: status == 'RUNNING', 'resume')
        command('run.stop')
        poll_until(lambda: status == 'STOPPED', 'safe stop and drain')
        command('run.stop')
        # Flush the repeated stop and keep the connection responsive.
        for _ in range(10):
            poll_until(lambda: True, 'repeat stop')
            time.sleep(0.01)
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if client.status == 'CONNECTED' and status in {'STARTING', 'RUNNING', 'PAUSED'}:
            try:
                command('run.stop')
                poll_until(lambda: status in {'STOPPED', 'FAILED'}, 'cleanup after probe failure')
            except Exception as cleanup_exc:
                report['cleanup_error'] = f'{type(cleanup_exc).__name__}: {cleanup_exc}'
        client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--mode', choices=['demo', 'interactive_training', 'replay'], default='interactive_training')
    parser.add_argument('--options', default='{}', help='run.start options as JSON')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.port, json.loads(args.options), args.timeout, args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'passed': result['passed'], 'messages': len(result['messages']),
                      'error': result.get('error'), 'evidence': str(args.output)}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

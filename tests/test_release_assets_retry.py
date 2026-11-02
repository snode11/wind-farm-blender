"""Execute the draft-upload step against a local gh mock, including partial failure."""
from pathlib import Path
import json
import os
import subprocess
import sys
import textwrap
import pytest

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('extension.zip', 'source.zip', 'source.zip.sha256', 'delivery.json')


@pytest.fixture
def upload(tmp_path):
    state_path = tmp_path / 'state.json'
    gh = tmp_path / 'gh'
    gh.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''
        import json,os,sys
        from pathlib import Path
        state_path = Path(os.environ['MOCK_GH_STATE'])
        state = json.loads(state_path.read_text())
        args = sys.argv[1:]
        if args[:2] == ['release','view']:
            if 'isDraft' in args:
                print('true' if state['draft'] else 'false')
            else:
                print(json.dumps({'assets': [{'name': name, 'state': 'uploaded'} for name in state['uploaded']]}))
        elif args[:2] == ['release','upload']:
            name = Path(args[3]).name
            state['attempted'].append(name)
            if state.get('fail_once') == name:
                del state['fail_once']
                state_path.write_text(json.dumps(state))
                sys.exit(1)
            assert name not in state['uploaded']
            state['uploaded'].append(name)
            state_path.write_text(json.dumps(state))
        else:
            sys.exit('Unexpected mock gh command')
    '''))
    gh.chmod(0o755)
    python = tmp_path / 'python'
    python.symlink_to(sys.executable)
    block = (ROOT / '.github/workflows/release-assets.yml').read_text().rsplit('        run: |\n', 1)[1]
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], MOCK_GH_STATE=str(state_path),
               RELEASE_TAG='v0.3.42.4', GITHUB_REPOSITORY='test/local-only',
               EXTENSION_ARCHIVE=str(tmp_path / NAMES[0]), SOURCE_ARCHIVE=str(tmp_path / NAMES[1]),
               DELIVERY_RECORD=str(tmp_path / NAMES[3]))
    def run(uploaded, *, draft=True, fail_once=None):
        state_path.write_text(json.dumps(dict(uploaded=list(uploaded), draft=draft, attempted=[], fail_once=fail_once)))
        def execute():
            result = subprocess.run(['bash', '-e', '-c', textwrap.dedent(block)], env=env, capture_output=True, text=True)
            return result, json.loads(state_path.read_text())
        return execute
    return run


@pytest.mark.parametrize('missing', [NAMES, NAMES[2:], (NAMES[2],), (NAMES[3],), ()])
def test_each_missing_attachment_is_uploaded_independently(upload, missing):
    result, state = upload([name for name in NAMES if name not in missing])()
    assert result.returncode == 0, result.stderr
    assert state['attempted'] == list(missing)
    assert set(state['uploaded']) == set(NAMES)


@pytest.mark.parametrize('failed', NAMES[1:])
def test_partial_failure_then_rerun_fills_remaining_attachments(upload, failed):
    execute = upload([NAMES[0]], fail_once=failed)
    result, partial = execute()
    assert result.returncode != 0
    result, complete = execute()
    assert result.returncode == 0, result.stderr
    assert set(complete['uploaded']) == set(NAMES)
    assert all(complete['attempted'].count(name) == (2 if name == failed else 1) for name in NAMES[1:])


def test_public_release_rejects_upload_before_any_mutation(upload):
    result, state = upload([], draft=False)()
    assert result.returncode != 0
    assert state['attempted'] == [] and state['uploaded'] == []

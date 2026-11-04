"""Real Bridge identities rotate within an owned instance without following UI connections."""
import ast
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from wfrl.blender_bridge import launcher_state as state

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('binding_launcher',ROOT/'scripts/blender/wfrl_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@contextmanager
def bridge(tmp_path, name):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0))
        port = reservation.getsockname()[1]
    path = tmp_path/(name+'.json')
    token = uuid.uuid4().hex
    owner = dict(schema=state.OWNER_SCHEMA,run_token=token,host='127.0.0.1',port=port)
    state.atomic_write(path,owner)
    env = dict(os.environ,**{state.RUN_TOKEN:token,state.BINDING_FILE:str(state.binding_path(path))},
               PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.Popen([sys.executable,'-m','wfrl.blender_bridge','--fake','--port',str(port)],
        cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline = time.monotonic()+5
        while not state.binding_path(path).exists():
            assert process.poll() is None,process.communicate()
            assert time.monotonic()<deadline
            time.sleep(.01)
        assert state.read_binding(path,owner) is not None
        yield path,owner,process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_owned_session_rotation_and_frontend_switch_keep_a_and_b_separate(tmp_path,monkeypatch):
    with bridge(tmp_path,'A') as (a,owner_a,process_a), bridge(tmp_path,'B') as (b,owner_b,process_b):
        first = launcher.confirm_idle_bridge(owner_a['port'],2)
        second = launcher.confirm_idle_bridge(owner_a['port'],2)
        other = launcher.confirm_idle_bridge(owner_b['port'],2)
        assert first and second and other and first!=second
        binding_a = state.read_binding(a,owner_a)
        assert binding_a['session_id']==second and binding_a['session_generation']==1
        before = a.read_bytes(),state.binding_path(a).read_bytes(),state.binding_path(b).read_bytes()
        source = ROOT/'scripts/blender/wfrl_blender_bootstrap.py'
        function = next(node for node in ast.parse(source.read_text()).body
                        if isinstance(node,ast.FunctionDef) and node.name=='_sync_backend_scene')
        namespace = dict(_scene_requested=False,os=os,addon=SimpleNamespace(runtime=SimpleNamespace(
            _client=SimpleNamespace(session_id=other),
            get_state=lambda:SimpleNamespace(connection='CONNECTED',confirmed=True,error='controlled UI error'))))
        monkeypatch.setenv('WFRL_SESSION_FILE',str(a))
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
        assert namespace['_sync_backend_scene']()==.25
        assert (a.read_bytes(),state.binding_path(a).read_bytes())==before[:2]
        assert launcher.stop_session(SimpleNamespace(session_file=str(a),port=None,timeout=2))==0
        assert process_a.poll() is None and process_b.poll() is None
        assert state.binding_path(b).read_bytes()==before[2]


def test_recovery_rejects_port_conflict_and_reused_process_identity(tmp_path,monkeypatch):
    with bridge(tmp_path,'owner') as (path,owner,process):
        request=Mock(return_value=True)
        monkeypatch.setattr(launcher,'request_protocol_stop',request)
        different=owner['port']-1 if owner['port']>1024 else owner['port']+1
        with pytest.raises(RuntimeError,match='conflicting'):
            launcher.stop_session(SimpleNamespace(session_file=str(path),port=different,timeout=1))
        binding=json.loads(state.binding_path(path).read_text())
        binding['bridge']['create_time']+=1
        state.atomic_write(state.binding_path(path),binding)
        assert launcher.stop_session(SimpleNamespace(session_file=str(path),port=None,timeout=1))==1
        assert process.poll() is None and path.exists()
        request.assert_not_called()


def test_binding_cannot_be_reassigned_to_a_different_process(tmp_path):
    path=tmp_path/'binding.json'
    writer=state.SessionBinding(path,'token','127.0.0.1',8765)
    writer.publish('127.0.0.1',8765,'first')
    writer.publish('127.0.0.1',8765,'second')
    saved=path.read_bytes()
    assert json.loads(saved)['session_generation']==1
    replacement=state.SessionBinding(path,'token','127.0.0.1',8765)
    replacement.identity=dict(replacement.identity,pid=replacement.identity['pid']+1)
    with pytest.raises(RuntimeError,match='different process'):
        replacement.publish('127.0.0.1',8765,'unrelated')
    assert path.read_bytes()==saved

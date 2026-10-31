"""Native NREL/GW184 editor commits and history survive deferred mesh disposal."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT), str(ROOT / 'projects/gw184-single')]
import wfrl_blender as addon
from wfrl_blender.nrel_defects import editor, defect_geometry, defect_schema
from gw184 import defect_editor as gw_editor, defect_geometry as gw_geometry, defect_schema as gw_schema

OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)
assert bpy.app.background
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
bpy.ops.wfrl.farm_flex_view(turbine='all')
checks = {}


class Proxy:
    def __init__(self, source, **overrides):
        self.source, self.overrides = source, overrides
    def __getattr__(self, name):
        return self.overrides[name] if name in self.overrides else getattr(self.source, name)


def exercise(name, session, module, schema, document_key):
    rows = []
    for operation in ('commit', 'restore', 'undo', 'redo'):
        document = session.store.document
        if not document['defects']:
            document['defects'] = [schema.new_defect(session.surface, 'fine_crack')]
        else:
            document['defects'][0]['enabled'] = not document['defects'][0]['enabled']
            document['defects'][0]['revision'] += 1
        if operation in ('undo', 'redo'):
            session.store.restore(document)
        if operation == 'redo':
            session.store.undo()
        if operation == 'commit':
            session.begin(0 if session.store.document['defects'] else None, 'fine_crack')
            session.draft['defects'][session.draft_index]['shape']['length_m'] *= 1.01
            session.draft['defects'][session.draft_index]['revision'] += 1
        old = session.store.resource
        child = old['turbines']['T1'] if name == 'NREL' else old
        victim = child['meshes'][1]
        victim_name = victim.name
        version = session.store.version
        real_bpy = module.bpy
        failures = []
        def remove_mesh(mesh):
            if mesh == victim:
                failures.append(mesh)
                raise RuntimeError('injected post-commit mesh release failure')
            return real_bpy.data.meshes.remove(mesh)
        module.bpy = Proxy(real_bpy, data=Proxy(real_bpy.data,
            meshes=Proxy(real_bpy.data.meshes, remove=remove_mesh)))
        try:
            if operation == 'commit':
                session.finish(True)
                assert session.draft is None and session.edit_state is None
            elif operation == 'restore':
                session.store.restore(document)
            else:
                assert getattr(session.store, operation)()
        finally:
            module.bpy = real_bpy
        assert failures and session.store.version == version + 1
        assert json.loads(session.scene[document_key]) == session.store.document
        assert session.scene['defect_state_version'] == session.store.version
        assert old in session.store.pending_disposals and session.store.cleanup_errors
        assert session.store.can_redo if operation == 'undo' else session.store.can_undo
        assert not child.get('_disposed', False)
        session.store.retry_disposals(strict=True)
        assert not session.store.pending_disposals and not session.store.cleanup_errors
        assert child['_disposed'] and bpy.data.meshes.get(victim_name) is None
        assert session.store.version == version + 1
        rows.append(dict(operation=operation, version=session.store.version,
                         document_and_ui_consistent=True, history_consistent=True, disposal_retried=True))
    return rows

s = editor.ensure(scene)
checks['NREL'] = exercise('NREL', s, defect_geometry, defect_schema, editor.DOCUMENT_KEY)
editor.detach(scene)
assert editor.session() is None
checks['NREL_normal_detach_after_pending_cleanup'] = True
s = gw_editor.EditorSession()
checks['GW184'] = exercise('GW184', s, gw_geometry, gw_schema, 'defect_document')
addon.unregister()
result = dict(status='PASS', blender=bpy.app.version_string, module_file=addon.__file__, checks=checks)
(OUT / 'result.json').write_text(json.dumps(result, indent=2))
print('DEFECT_COMMIT_CLEANUP_PASS', json.dumps(result), flush=True)

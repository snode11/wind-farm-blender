"""Native RNA list identity and action gating, using real editor transactions."""
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender.nrel_defects import editor, defect_schema as schema

addon.register()
addon.load_demo_scene()
s = editor.ensure(bpy.context.scene)
p = s.scene.wfrl_nrel_defects
assert hasattr(p, 'defect_items'), 'Editor must expose a synchronized clickable defect list'
assert len(p.defect_items) == 0 and p.selected == -1
assert editor.action_unavailable(s, p, 'EDIT')
assert editor.action_unavailable(s, p, 'UNDO')
assert not editor.action_unavailable(s, p, 'NEW')

doc = s.store.document
doc['defects'] = [schema.new_defect(s.surface, 'fine_crack', turbine_id=tid)
                  for tid in ('T1', 'T2', 'T3')]
s.store.restore(doc)
ids = [d['id'] for d in doc['defects']]
assert [item.defect_id for item in p.defect_items] == ids
p.selected = 2
s.mutate('DELETE', 0)
assert p.selected == 1 and p.defect_items[p.selected].defect_id == ids[2]
s.store.undo()
assert p.selected == 2 and p.defect_items[p.selected].defect_id == ids[2]

def action(code):
    assert bpy.ops.wfrl.nrel_defect_action(action=code) == {'FINISHED'}, code

action('COPY')
copy_id = s.store.document['defects'][-1]['id']
assert p.selected == 3 and p.defect_items[3].defect_id == copy_id
assert not p.defect_items[3].enabled
action('DELETE')
assert p.selected == 2 and p.defect_items[2].defect_id == ids[2]
action('EDIT')
assert s.draft['defects'][s.draft_index]['id'] == ids[2]
for code in ('NEW', 'EDIT', 'DELETE', 'TOGGLE', 'UNDO', 'REDO', 'SAVE', 'LOAD',
             'HEALTHY', 'TIME', 'ANALYZE', 'EXPORT_G', 'EXPORT_A', 'SCAN'):
    assert editor.action_unavailable(s, p, code), code
for code in ('PREVIEW', 'CONFIRM', 'CANCEL', 'CLOSEUP'):
    assert not editor.action_unavailable(s, p, code), code
original = s.store.document
p.width *= 1.1
action('PREVIEW')
assert s.store.document == original
action('CANCEL')
assert s.store.document == original and p.defect_items[p.selected].defect_id == ids[2]

p.selected = 0
action('NEW')
assert len(p.defect_items) == 4 and p.selected == 3
action('CANCEL')
assert len(p.defect_items) == 3 and p.selected == 0
action('NEW')
p.blade_id = 2  # Keep the new defect clear of the existing enabled instance.
action('CONFIRM')
assert len(p.defect_items) == 4 and p.selected == 3
assert p.defect_items[3].defect_id == s.store.document['defects'][3]['id']

action('HEALTHY')
assert not editor.action_unavailable(s, p, 'HEALTHY')
assert editor.action_unavailable(s, p, 'EDIT')
action('HEALTHY')
s.busy = True
for code in ('HEALTHY', 'CLOSE', 'NEW', 'SCAN'):
    assert editor.action_unavailable(s, p, code), code
s.busy = False

empty = schema.new_document(s.surface)
s.store.restore(empty)
assert len(p.defect_items) == 0 and p.selected == -1
assert editor.action_unavailable(s, p, 'HEALTHY')
action('UNDO')
assert len(p.defect_items) == 4 and 0 <= p.selected < 4
action('REDO')
assert len(p.defect_items) == 0 and p.selected == -1

result = dict(status='PASS', module_file=editor.__file__,
              list_identity=True, draft_cancel_and_confirm=True,
              undo_redo_empty_list=True, action_gating=True,
              scope='Native background RNA and transactions; no visible UI acceptance')
out = Path(os.environ['WFRL_TEST_OUTPUT'])
out.mkdir(parents=True, exist_ok=True)
(out/'ui-state-checks.json').write_text(json.dumps(result, indent=2))
print('NREL_DEFECT_UI_STATE_PASS', json.dumps(result), flush=True)

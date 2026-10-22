"""State provenance, bounded history and import impact: no Blender required."""
from copy import deepcopy
from types import SimpleNamespace as NS
import pytest
from wfrl_blender import custom_camera_preview as preview, custom_cameras as core
from wfrl_blender.custom_camera_history import History


def request(**changes):
    return dict(identity=('scene', 'source'), frame=3, subframe=.25, time_s=None,
                revision=1, profile='sRGB', long_edge=640, slots=(1,),
                cameras=((42, False, 'fov=75', (1, 0, 0)),), **changes)


class Image:
    width, height = 640, 480
    freed = False
    def free(self):self.freed=True


CONTEXT = NS(view_layer=NS(update=lambda: None))


def test_preview_failure_keeps_image_and_bound_metadata(monkeypatch):
    state=request()
    monkeypatch.setattr(preview,'provenance',lambda *_: deepcopy(state))
    monkeypatch.setattr(preview,'render_group',lambda *_: {1:Image()})
    p=preview.Preview();p.refresh(CONTEXT,[])
    original=p.images[1];metadata=deepcopy(p.metadata)
    assert p.status()=='已更新'
    state['cameras']=((42,False,'fov=80',(1,0,0)),)
    p.observe(None,[])
    assert '参数' in p.status() and '等待更新' in p.status()
    p.last=0
    def failure(*_):raise RuntimeError('GPU allocation failed')
    monkeypatch.setattr(preview,'render_group',failure)
    p.refresh(CONTEXT,[])
    assert p.images[1] is original and not original.freed
    assert p.metadata==metadata and '更新失败，显示上次画面' in p.status()
    monkeypatch.setattr(preview,'render_group',lambda *_: {1:Image()})
    p.attempt=0;p.refresh(CONTEXT,[])
    assert original.freed and p.metadata['cameras']==state['cameras'] and not p.error
    p.free()


def test_session_change_clears_old_image_and_static_uses_cached_frame(monkeypatch):
    state=request()
    monkeypatch.setattr(preview,'provenance',lambda *_: deepcopy(state))
    monkeypatch.setattr(preview,'render_group',lambda *_: {1:Image()})
    p=preview.Preview();p.refresh(CONTEXT,[])
    state.update(frame=9,subframe=.75)
    p.observe(None,[])
    assert preview.clock_text(p.metadata['time_s'],p.metadata['frame'],p.metadata['subframe'])=='帧 3 + 0.250 · 无仿真时钟'
    old=p.images[1];state['identity']=('scene','new source')
    p.observe(None,[])
    assert old.freed and p.metadata is None and p.status()=='画面准备中'
    p.free()


def test_mixed_group_or_metadata_never_replaces_good_group(monkeypatch):
    state=request()
    monkeypatch.setattr(preview,'provenance',lambda *_: deepcopy(state))
    monkeypatch.setattr(preview,'render_group',lambda *_: {1:Image()})
    p=preview.Preview();p.refresh(CONTEXT,[]);old=p.images
    state['frame']=4;p.last=0
    bad=Image()
    def interrupted(*_):state['frame']=5;return {1:bad}
    monkeypatch.setattr(preview,'render_group',interrupted)
    p.refresh(CONTEXT,[])
    assert p.images is old and p.metadata['frame']==3 and bad.freed
    p.free()


def test_history_limit_noop_and_external_divergence():
    h=History()
    for n in range(25):h.record('session',{'n':n},str(n),str(n+1),f'edit {n}')
    assert len(h.entries)==20 and h.entries[0].before=={'n':5}
    h.record('session',{'n':25},'25','25','noop')
    assert len(h.entries)==20
    assert not h.check('session','external') and '外部' in h.notice
    h.record('session',{},'a','b','edit')
    assert not h.check('new session','b') and '会话' in h.notice


def test_three_slot_import_summary():
    def record(slot,label='original'):
        return dict(slot_id=slot,label=label,enabled=True,parameters={'fov':75})
    old={'cameras':[record(1),record(3)]}
    new={'cameras':[record(1,'new'),record(2)]}
    rows=core.layout_summary(old,new)
    assert [r['action'] for r in rows]==['将替换','将创建','将清空']
    assert '备注名' in rows[0]['changes'][0]
    assert core.layout_summary(old,deepcopy(old))[0]['action']=='配置不变'


@pytest.mark.parametrize('width,scale',[(650,1),(900,1.5),(1200,2)])
def test_persistent_actions_never_cover_card_images(width,scale):
    rect=(50,20,width,800)
    for button in preview.action_buttons(rect,scale):
        assert preview.contains(rect,button[0],button[1])
        for card in preview.cells(rect,scale,editing=True):
            assert card[1]+card[3]<button[1]


def test_import_summary_ignores_json_tuple_list_roundtrip():
    import json
    old={'cameras':[dict(slot_id=1,label='old',enabled=True,parameters={'location':(1.,2.,3.),'fov':75.},anchor={'point':(1.,2.,3.)})]}
    incoming=json.loads(json.dumps(old));incoming['cameras'][0]['label']='new'
    assert core.layout_summary(old,incoming)[0]['changes']==['备注名：old → new']

"""Committed defect configuration and Blender save/reopen callbacks."""
import json
import logging
from pathlib import Path
from bpy.app.handlers import persistent
from .defect_schema import validate_document, save_document

_LOG = logging.getLogger(__name__)


class EditorPersistence:

    """Configuration I/O and live-state suspension during Blender saves."""

    def suspend_for_save(self):
        """Serialize committed geometry without consuming the live edit transaction."""
        if self.save_state is not None:
            self.resume_after_save()
        if self.draft is None and self.healthy_payload is None:
            return
        p = self.scene.wfrl_nrel_defects
        self.save_state = dict(active=self.geometry.active,
            healthy_payload=self.healthy_payload, status=p.status,
            records=self.records, analysis_status=self.scene['defect_analysis_status'])
        self.healthy_payload = None
        if self.geometry.active is not self.payload:
            self.geometry.activate(self.payload)
        self.update_helpers(self.store.document)
        p.status = '文件仅包含已应用的缺陷配置'

    def resume_after_save(self, saved=False):
        """Restore the same preview, draft inputs and comparison after a save attempt."""
        state = self.save_state
        if state is None:
            return
        self.healthy_payload = state['healthy_payload']
        if self.geometry.active is not state['active']:
            self.geometry.activate(state['active'])
        self.update_helpers(state['active']['document'])
        self.records = state['records']
        self.scene['defect_analysis_status'] = state['analysis_status']
        p = self.scene.wfrl_nrel_defects
        p.status = ('文件已保存已应用配置；草稿仍保留，需应用后再保存'
                    if saved and self.draft is not None else state['status'])
        self.save_state = None

    def save(self,path):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        save_document(path,self.store.document,self.surface)

    def load(self,path):
        if self.draft is not None: raise ValueError('请先确认或取消编辑')
        self.store.restore(validate_document(json.loads(Path(path).read_text(encoding='utf-8')),self.surface))


def restore_saved(scene):
    from . import editor as _editor
    if scene.get('wfrl_nrel_defect_enabled') and scene.get(_editor.DOCUMENT_KEY):
        try: _editor.ensure(scene)
        except Exception as exc:
            _LOG.exception('Saved defect configuration could not be restored')
            try:
                scene['defect_analysis_status']='RESTORE_FAILED: '+str(exc)
                scene.wfrl_nrel_defects.status=str(exc)
            except (AttributeError, ReferenceError):
                _LOG.exception('Defect restore failure could not be displayed')
            raise


@persistent
def save_pre(_unused):
    # The file contains committed state; live drafts and previews remain reusable.
    from . import editor as _editor
    try:
        for s in list(_editor._SESSIONS.values()):
            scan = getattr(s, 'scan_operator', None)
            if scan is not None:
                scan.finish_scan(None, True, resume_playback=False)
            s.suspend_for_save()
    except Exception:
        _LOG.exception('Defect editor save preparation failed')
        try:
            _editor.restore_after_save()
        except Exception:
            _LOG.exception('Defect editor save preparation rollback also failed')
        raise


def restore_after_save(saved=False):
    from . import editor as _editor
    errors = []
    for s in list(_editor._SESSIONS.values()):
        try:
            s.resume_after_save(saved)
        except Exception as exc:
            errors.append(exc)
            _LOG.exception('Defect editor state could not be restored after save')
            try:
                s.scene.wfrl_nrel_defects.status = '保存后恢复编辑状态失败：'+str(exc)
            except (AttributeError, ReferenceError):
                _LOG.exception('Defect save recovery failure could not be displayed')
    if errors:
        raise errors[0]


@persistent
def save_post(_unused):
    from . import editor as _editor
    _editor.restore_after_save(saved=True)


@persistent
def save_post_fail(_unused):
    from . import editor as _editor
    _editor.restore_after_save()

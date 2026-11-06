"""Observation resource cleanup, save restoration and playback ownership."""


def alive_window(self):
    bpy = self._native['bpy']
    # Never dereference a cached Window RNA after its OS window was closed.
    return next((w for w in bpy.context.window_manager.windows
                 if w.as_pointer() == self.window_id), None)


def clear_views(self):
    bpy = self._native['bpy']
    errors = []
    for name, owner in (('handle', bpy.types.SpaceView3D),
                        ('diagram_handle', bpy.types.SpaceImageEditor)):
        handle = getattr(self, name)
        if handle is None:
            continue
        try:
            owner.draw_handler_remove(handle, 'WINDOW')
            setattr(self, name, None)
        except (ReferenceError, ValueError):
            setattr(self, name, None)
        except Exception as exc:
            errors.append(f'移除相机绘制回调：{exc}')
    def detach_cameras():
        alive = self.alive_window()
        if alive and self.owns_window:
            for area in alive.screen.areas:
                if area.type == 'VIEW_3D':
                    self._native['_cleanup_step'](errors, '解除观察相机绑定',
                                  lambda area=area: setattr(area.spaces.active, 'camera', None))
    self._native['_cleanup_step'](errors, '解除观察相机绑定', detach_cameras)
    for entry in self.entries:
        self._native['_cleanup_step'](errors, '清理观察相机', lambda entry=entry: self.release_proxy(entry))
    # Retain only unreleased resources so a later shutdown can retry them.
    self.entries[:] = [e for e in self.entries
                       if e.get('proxy') is not None or e.get('proxy_data') is not None]
    self.diagram_area = None
    return errors


def suspend_for_save(self):
    """Serialize ordinary cameras and original quality, retaining live views."""
    bpy = self._native['bpy']
    core = self._native['core']
    if self.save_state is not None or self.closing:
        return
    state = {'quality': self.quality_settings(), 'shading': [], 'views': []}
    self.save_state = state
    if self.preview_quality is not None:
        self.set_quality(self.preview_quality)
    for window_id, area_id, original in self.source_shading:
        window = next((w for w in bpy.context.window_manager.windows
                       if w.as_pointer() == window_id), None)
        area = next((a for a in window.screen.areas if a.as_pointer() == area_id), None) if window else None
        if area and area.type == 'VIEW_3D':
            shader = area.spaces.active.shading
            state['shading'].append((shader, shader.type))
            shader.type = original
    errors = []
    for entry in self.entries:
        space = entry['area'].spaces.active
        if space.type == 'VIEW_3D':
            state['views'].append((entry, self._native['_view_state'](space)))
            space.camera = core.get_camera(self.scene, entry['slot'])
            space.use_local_camera = True
        errors.extend(self.release_proxy(entry))
        entry['key'] = entry['view_key'] = None
    if errors:
        raise RuntimeError('；'.join(errors))


def resume_after_save(self):
    """Restore the same live inspection after either save result."""
    state, self.save_state = self.save_state, None
    if state is None:
        return
    errors = []
    self._native['_cleanup_step'](errors, '恢复观察画质', lambda: self.set_quality(state['quality']))
    for shader, value in state['shading']:
        self._native['_cleanup_step'](errors, '恢复主视口', lambda shader=shader, value=value: setattr(shader, 'type', value))
    self._native['_cleanup_step'](errors, '恢复观察相机', self.sync)
    for entry, view in state['views']:
        self._native['_cleanup_step'](errors, '恢复观察视角', lambda entry=entry, view=view:
                      self._native['_restore_view'](entry['area'].spaces.active, view))
    if errors:
        raise RuntimeError('；'.join(errors))


def playback_context(self):
    """Own user playback in a surviving source area, including its region."""
    bpy = self._native['bpy']
    windows=[w for w in bpy.context.window_manager.windows if w.scene==self.scene]
    source=next((w for w in windows if w.as_pointer()==self.source_window_id),None)
    if source is None:source=next((w for w in windows if w!=self.window),None)
    if source is None:source=next((w for w in windows if w==self.window),None)
    if source is None:return None
    area=next((a for a in source.screen.areas if a.type=='VIEW_3D'),None)
    if area is None:return None
    region=next((r for r in area.regions if r.type=='WINDOW'),None)
    return {'window':source,'area':area,'region':region}


def lifecycle_event(self, event, **details):
    bpy = self._native['bpy']
    import time
    self.lifecycle.append({'event':event,'wall_s':time.monotonic(),'frame':self.scene.frame_current,
                           'windows':[(w.as_pointer(),w.screen.is_animation_playing,w.scene.frame_current)
                                      for w in bpy.context.window_manager.windows],**details})


def close(self, preserve_playback=True):
    bpy = self._native['bpy']
    if self.closed:
        return True
    errors = []
    first_close = not self.closing
    self.closing = True
    if not preserve_playback:
        self.resume_when_closed = False

    def finish_capture():
        from . import custom_camera_capture
        job = custom_camera_capture._ACTIVE
        if job and job.scene == self.scene and job.window == self.window:
            job.finish(error='相机观察窗口已关闭', restore=preserve_playback)

    def transfer_playback():
        if not first_close:
            return
        from . import playback
        # is_animation_playing is global, not the timer's owner. Transfer
        # only playback owned by this scene before its window is destroyed.
        alive = self.alive_window()
        playing = bool(alive and preserve_playback and playback.is_playing(self.scene))
        self.resume_when_closed = playing
        self.lifecycle_event('close_before', preserve_playback=preserve_playback, playing=playing)
        resume_context = self.playback_context() if playing else None
        if resume_context:
            with bpy.context.temp_override(**resume_context):
                result = bpy.ops.screen.animation_cancel(restore_frame=False)
                self.lifecycle_event('cancel_before_close', result=list(result))
                self.resume_generation = playback.generation()

    try:
        self._native['_cleanup_step'](errors, '结束相机采集', finish_capture)
        self._native['_cleanup_step'](errors, '转移共同播放', transfer_playback)
        self._native['_cleanup_step'](errors, '清理相机视图', self.clear_views)
    finally:
        try:
            self._native['_cleanup_step'](errors, '恢复预览画质', self.restore_quality)
            self._native['_cleanup_step'](errors, '恢复主视口画质', self.restore_source_shading)
        finally:
            self._native['_cleanup_step'](errors, '关闭观察窗口', self.close_window)
            self.preparing = False
            self.diagram = None
            self.diagram_batches = None
    if self.resume_when_closed and not self.owns_window and not self.resume_scheduled:
        # Window/timer destruction is finalized after this event.
        def schedule_resume():
            bpy.app.timers.register(self.resume_after_close, first_interval=.01)
            self.resume_scheduled = True
        self._native['_cleanup_step'](errors, '恢复共同播放', schedule_resume)
    self.closed = not (self.entries or self.handle is not None or self.diagram_handle is not None
                       or self.preview_quality is not None or self.source_shading or self.owns_window
                       or (self.resume_when_closed and not self.resume_scheduled))
    if errors:
        self._native['_remember_error'](self.scene, '相机观察恢复未完成：' + '；'.join(errors), append=True)
    return self.closed


def restore_quality(self):
    if self.preview_quality is not None:
        try:
            self.set_quality(self.preview_quality)
        except ReferenceError:
            # A removed scene has no remaining preview settings to restore.
            self.preview_quality = None
            return
        self.preview_quality = None


def restore_source_shading(self):
    bpy = self._native['bpy']
    pending, errors = [], []
    for window_id, area_id, shading_type in self.source_shading:
        try:
            source = next((w for w in bpy.context.window_manager.windows
                           if w.as_pointer() == window_id), None)
            if source:
                area = next((a for a in source.screen.areas if a.as_pointer() == area_id), None)
                if area and area.type == 'VIEW_3D':
                    area.spaces.active.shading.type = shading_type
        except ReferenceError:
            pass
        except Exception as exc:
            pending.append((window_id, area_id, shading_type))
            errors.append(f'恢复主视口画质：{exc}')
    self.source_shading[:] = pending
    return errors


def close_window(self):
    bpy = self._native['bpy']
    alive = self.alive_window()
    if alive and self.owns_window:
        with bpy.context.temp_override(window=alive):
            if bpy.ops.wm.window_close() != {'FINISHED'}:
                raise RuntimeError('观察窗口关闭操作未完成')
    self.owns_window = False
    self.lifecycle_event('window_closed')


def resume_after_close(self):
    bpy = self._native['bpy']
    try:
        from . import playback
        self.lifecycle_event('resume_event')
        # Another scene may have started during this delayed restoration.
        # An unknown live owner is protected by the global flag as well.
        owner = playback.owner_scene()
        if (owner is not None and owner != self.scene) or playback.any_playing():
            self.lifecycle_event('resume_skipped', reason='another_or_live_player')
            return None
        if playback.generation() != self.resume_generation:
            self.lifecycle_event('resume_skipped', reason='playback_state_changed')
            return None
        resume_context=self.playback_context()
        if resume_context:
            with bpy.context.temp_override(**resume_context):
                result=bpy.ops.screen.animation_play()
                self.lifecycle_event('resume_result',result=list(result))
    except Exception as exc:
        self.lifecycle.append({'event':'resume_error','error':str(exc)})
        self._native['_remember_error'](self.scene, f'恢复共同播放失败：{exc}', append=True)
    return None

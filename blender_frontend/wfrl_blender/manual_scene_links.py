"""Persistent manual-scene lookup without a cyclic pair of Scene ID references."""

OWNER_KEY = 'wfrl_manual_scene_owner'


def bind(source, target):
    if source.library is not None:
        raise ValueError('不能改写链接场景的手动关联')
    if target is None:
        # An empty token preserves explicit unlinking across save and restore.
        source[OWNER_KEY] = ''
    else:
        import bpy
        if (not isinstance(target, bpy.types.Scene)
                or target.get('wfrl_scene_kind') != 'manual'
                or not target.get('wfrl_manual_owner')
                or target.wfrl_manual_return_scene != source):
            raise ValueError('手动场景及返回归属不匹配')
        source[OWNER_KEY] = target['wfrl_manual_owner']
    source.pop('wfrl_manual_scene', None)


def resolve(source):
    import bpy
    saved = source.get('wfrl_manual_scene')
    if saved is not None:
        valid = (isinstance(saved, bpy.types.Scene) and saved.get('wfrl_scene_kind') == 'manual'
                 and saved.get('wfrl_manual_owner')
                 and saved.wfrl_manual_return_scene == source)
        if source.library is not None:
            return saved if valid else None
        if valid:
            source[OWNER_KEY] = saved['wfrl_manual_owner']
        source.pop('wfrl_manual_scene', None)
    owner = source.get(OWNER_KEY)
    if owner is not None and not owner:
        return None
    # Saved RNA pointers may load without a matching ID-property value.
    candidates = [scene for scene in bpy.data.scenes
                  if scene.get('wfrl_scene_kind') == 'manual'
                  and scene.get('wfrl_manual_owner')
                  and (not owner or scene.get('wfrl_manual_owner') == owner)
                  and scene.wfrl_manual_return_scene == source]
    target = candidates[0] if len(candidates) == 1 else None
    if target is not None and not owner and source.library is None:
        source[OWNER_KEY] = target['wfrl_manual_owner']
    return target


def restore():
    """Resolve stored links before evaluating scene copies or replacing mesh data."""
    import bpy
    if hasattr(bpy.data, 'scenes'):
        for scene in bpy.data.scenes:
            if scene.library is None:
                resolve(scene)

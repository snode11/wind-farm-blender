"""Video preflight errors remain available after the operator report closes."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_camera_action_ownership import execute_body


@pytest.mark.parametrize('kind', ['Export', 'Stream'])
def test_preflight_failure_remains_in_video_page(kind):
    detail = '输入文件或输出设置不可用\n' + 'long-path/' * 30 + '完整失败原因'
    export = SimpleNamespace(active=False, start=Mock(side_effect=ValueError(detail)), error='', message='就绪')
    stream = SimpleNamespace(active=False, start=Mock(side_effect=ValueError(detail)), state='STOPPED', message='未启动')
    namespace = {'service': SimpleNamespace(export=export, stream=stream),
                 '_source': lambda _context: '/source.mp4', '_watch': Mock(),
                 'bpy': SimpleNamespace(path=SimpleNamespace(abspath=lambda path: path)),
                 'get_preferences': lambda _context: SimpleNamespace(video_ffmpeg='', video_mediamtx='',
                     video_rtsp_port=8554, video_rtsp_lan=False)}
    execute = execute_body('video_output.py', 'WFRL_OT_Video' + kind, namespace)
    assert execute(SimpleNamespace(filepath='/output.mp4', report=Mock()), object()) == {'CANCELLED'}
    if kind == 'Export':
        assert export.error == detail
    else:
        assert stream.state == 'ERROR'
        assert stream.message == detail
    namespace['_watch'].assert_not_called()

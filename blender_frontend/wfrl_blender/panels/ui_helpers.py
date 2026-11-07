"""Readable result messages and complete reports in narrow task panels."""
from pathlib import Path
import unicodedata


def message(layout, text, icon='INFO', *, context=None, max_lines=None):
    if context is None:
        import bpy
        context = bpy.context
    region = getattr(context, 'region', None)
    preferences = getattr(context, 'preferences', None)
    scale = getattr(getattr(preferences, 'system', None), 'ui_scale', 1.)
    width = max(8, int((getattr(region, 'width', 320) / max(scale, .5) - 72) / 7))
    lines, line, used = [], '', 0
    for char in str(text):
        size = 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
        if char == '\n' or (line and used + size > width):
            lines.append(line)
            line, used = '', 0
        if char != '\n':
            line += char
            used += size
    if line:
        lines.append(line)
    shown = lines if max_lines is None else lines[:max_lines]
    for index, line in enumerate(shown):
        layout.label(text=line, icon=icon if index == 0 else 'NONE')
    if len(shown) < len(lines):
        layout.label(text='完整内容见详情', icon='DOT')


def diagnostic(layout, title, detail, *, error=False, context=None):
    text = str(detail)
    message(layout, text, 'ERROR' if error else 'INFO', context=context, max_lines=4)
    full = title + '\n\n' + text
    row = layout.row(align=True)
    row.operator('wfrl.diagnostic_details', text='查看详情', icon='TEXT').report_text = full
    row.operator('wfrl.copy_diagnostic', text='复制错误' if error else '复制信息',
                 icon='COPYDOWN').report_text = full


def output_directory(layout, path, *, is_directory=False):
    directory = Path(path) if is_directory else Path(path).parent
    layout.operator('wm.path_open', text='打开输出目录', icon='FILE_FOLDER').filepath = str(directory)


def history_status(layout, status, *, context=None):
    state, separator, detail = status.partition(' / ')
    if not separator:
        message(layout, status, context=context)
    elif state == 'FAILED':
        diagnostic(layout, '历史导出失败', detail, error=True, context=context)
    else:
        layout.label(text={'COMPLETE': '历史导出完成', 'WRITING': '正在写入历史数据'}.get(state, state),
                     icon='CHECKMARK' if state == 'COMPLETE' else 'TIME')
        message(layout, '输出：' + detail, context=context)
        if state == 'COMPLETE':
            output_directory(layout, detail)

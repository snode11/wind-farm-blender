from pathlib import Path
import io
import json
import pytest
from PIL import Image
from wfrl_blender.custom_camera_capture import write_png,write_json


def test_png_preserves_display_bytes_and_flips_only_rows(tmp_path):
    # GPU rows start at lower left, PNG rows at upper left. Explicit asymmetric
    # colour/alpha fixture detects gamma reapplication, channels and orientation.
    bottom=bytes([0,0,255,255,17,91,143,128])
    top=bytes([255,0,0,255,0,255,0,255])
    target=tmp_path/'image.png'
    write_png(target,2,2,bottom+top)
    with Image.open(target) as image:
        assert list(image.getdata())==[(255,0,0,255),(0,255,0,255),(0,0,255,255),(17,91,143,128)]
    with pytest.raises(FileExistsError):write_png(target,2,2,bottom+top)
    with pytest.raises(ValueError):write_png(tmp_path/'bad.png',3,3,bottom)


def test_manifest_rejects_nan_and_preserves_committed_version(tmp_path):
    target=tmp_path/'manifest.json'
    write_json(target,{'status':'incomplete'})
    with pytest.raises(ValueError):write_json(target,{'time_s':float('nan')})
    assert json.loads(target.read_text())=={'status':'incomplete'}

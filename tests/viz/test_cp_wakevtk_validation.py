"""DisXY files must describe one complete finite XY velocity plane."""

import numpy as np
import pytest

from wfrl.viz.wakevtk import DisXYReader, read_disxy


def vtk_bytes(*, binary=False, values=None, title="DisXY velocity"):
    if values is None:
        values = np.array([[3, 4, 99], [5, 12, -99], [8, 15, 7],
                           [-7, 24, 2], [9, 40, 3], [20, 21, 4]], dtype=np.float32)
    header = (f"# vtk DataFile Version 3.0\n{title}\n"
              f"{'BINARY' if binary else 'ASCII'}\n"
              "DATASET STRUCTURED_POINTS\nDIMENSIONS 3 2 1\n"
              "ORIGIN -10 20 90\nSPACING 2 5 1\nPOINT_DATA 6\n"
              "VECTORS velocity float\n").encode()
    data = (np.asarray(values, dtype=">f4").tobytes() if binary else
            " ".join(map(str, np.asarray(values).ravel())).encode())
    return header + data + b"\n"


@pytest.mark.parametrize("binary", [False, True])
def test_cp_disxy_preserves_endianness_xy_order_and_horizontal_speed(tmp_path, binary):
    path = tmp_path / "Case.Low.DisXY01.007.vtk"
    path.write_bytes(vtk_bytes(binary=binary))
    frame = read_disxy(path)
    assert frame.t == 7 and frame.path == path
    np.testing.assert_array_equal(frame.x, [-10, -8, -6])
    np.testing.assert_array_equal(frame.y, [20, 25])
    np.testing.assert_array_equal(frame.u, [[5, 13, 17], [25, 41, 29]])
    assert frame.vec.shape == (2, 3, 3)
    assert frame.vec.dtype == frame.u.dtype == np.float32
    np.testing.assert_array_equal(frame.vec[1, 0], [-7, 24, 2])


@pytest.mark.parametrize("title", ["BINARY wind preview", "VECTORS in the title"])
def test_cp_disxy_title_is_not_parsed_as_header(tmp_path, title):
    path = tmp_path / "Case.Low.DisXY01.001.vtk"
    path.write_bytes(vtk_bytes(title=title))
    np.testing.assert_array_equal(read_disxy(path).u, [[5, 13, 17], [25, 41, 29]])


@pytest.mark.parametrize("original,replacement,field", [
    (b"# vtk DataFile Version 3.0", b"invalid signature", "VTK"),
    (b"ASCII\n", b"UNKNOWN\n", "ASCII|BINARY"),
    (b"DATASET STRUCTURED_POINTS", b"DATASET STRUCTURED_GRID", "DATASET"),
    (b"DIMENSIONS 3 2 1", b"DIMENSIONS 0 2 1", "DIMENSIONS"),
    (b"DIMENSIONS 3 2 1", b"DIMENSIONS 3 2 2", "DIMENSIONS|XY"),
    (b"DIMENSIONS 3 2 1", b"DIMENSIONS 3 2 1 9", "DIMENSIONS"),
    (b"ORIGIN -10 20 90", b"ORIGIN -10 20 1e999", "ORIGIN"),
    (b"SPACING 2 5 1", b"SPACING 0 5 1", "SPACING"),
    (b"SPACING 2 5 1", b"SPACING -2 5 1", "SPACING"),
    (b"SPACING 2 5 1", b"SPACING 2 5 1e999", "SPACING"),
    (b"POINT_DATA 6", b"POINT_DATA 5", "POINT_DATA"),
    (b"POINT_DATA 6\n", b"", "POINT_DATA"),
    (b"VECTORS velocity float", b"VECTORS velocity int", "VECTORS"),
    (b"VECTORS velocity float\n", b"", "VECTORS"),
    (b"ORIGIN -10 20 90", b"ORIGIN -10 20 90\nORIGIN 0 0 0", "ORIGIN"),
])
def test_cp_disxy_rejects_invalid_headers(tmp_path, original, replacement, field):
    path = tmp_path / "invalid.001.vtk"
    path.write_bytes(vtk_bytes().replace(original, replacement))
    with pytest.raises(ValueError, match=field) as error:
        read_disxy(path)
    assert str(path) in str(error.value)


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("count", [17, 19])
def test_cp_disxy_rejects_truncated_or_excess_vector_data(tmp_path, binary, count):
    path = tmp_path / "size.001.vtk"
    path.write_bytes(vtk_bytes(binary=binary, values=np.ones(count)))
    with pytest.raises(ValueError) as error:
        read_disxy(path)
    assert str(path) in str(error.value)


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_cp_disxy_rejects_nonfinite_velocity(tmp_path, binary, value):
    values = np.ones(18)
    values[2] = value
    path = tmp_path / "nonfinite.001.vtk"
    path.write_bytes(vtk_bytes(binary=binary, values=values))
    with pytest.raises(ValueError, match="finite|有限"):
        read_disxy(path)


@pytest.mark.parametrize("field", ["coordinates", "speed"])
def test_cp_disxy_rejects_derived_overflow(tmp_path, field):
    path = tmp_path / "overflow.001.vtk"
    blob = vtk_bytes(values=np.full(18, 3e38) if field == "speed" else None)
    if field == "coordinates":
        blob = blob.replace(b"SPACING 2 5 1", b"SPACING 1e308 5 1")
    path.write_bytes(blob)
    with pytest.raises(ValueError, match="finite|有限"):
        read_disxy(path)


def test_cp_disxy_reader_retries_repaired_frame_and_never_serves_zero_fill(tmp_path):
    first = tmp_path / "Case.Low.DisXY01.001.vtk"
    first.write_bytes(vtk_bytes(values=np.ones(2)))
    (tmp_path / "Case.Low.DisXY01.002.vtk").write_bytes(b"")
    reader = DisXYReader(tmp_path)
    assert reader.latest() is None
    first.write_bytes(vtk_bytes())
    assert reader.latest().t == 1
    assert reader.latest() is None


def test_cp_disxy_wait_timeout_uses_monotonic_time(tmp_path, monkeypatch):
    import time

    reader = DisXYReader(tmp_path)
    frame = object()
    samples = iter([None, frame])
    elapsed = [0.0]
    wall = iter([0.0, 0.0, 1000.0])
    monkeypatch.setattr(reader, "latest", lambda: next(samples))
    with monkeypatch.context() as clock:
        clock.setattr(time, "time", lambda: next(wall))
        clock.setattr(time, "monotonic", lambda: elapsed[0])
        clock.setattr(time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
        result = reader.wait_first(timeout=1.0, poll=0.1)
    assert result is frame


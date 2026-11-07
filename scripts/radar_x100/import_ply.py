"""Import an explicit PLY index without inferring file ordering or hardware time."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.radar_x100 import ply, schema, recording


def import_index(index_path, output):
    index_path = Path(index_path).resolve()
    raw_index = index_path.read_bytes()
    document = json.loads(raw_index)
    source = document['source_kind']
    if source not in {'MEASURED_REPLAY', 'GEOMETRY_SIMULATION', 'TEST'}:
        raise ValueError('Declare source_kind explicitly for a file recording')
    rows = document['frames']
    if not rows:
        raise ValueError('Explicit PLY index is empty')
    sink = recording.Recorder(output, description=dict(input_index_sha256=hashlib.sha256(raw_index).hexdigest(),
        timing='Explicit replay_time_s is a file presentation clock; physical time only in FrameTime'))
    session_id = document.get('session_id') or str(uuid.uuid4())
    try:
        previous_sequence = -1
        for row in rows:
            sequence = row['sequence']
            if type(sequence) is not int or sequence <= previous_sequence:
                raise ValueError('Explicit per-stream sequence must increase')
            previous_sequence = sequence
            path = Path(row['file'])
            if not path.is_absolute():
                path = index_path.parent / path
            time_fields = row.get('time', {})
            if 'fps' in document:
                if time_fields:
                    raise ValueError('Provide source time or explicit fps derivation, not both')
                fps = float(document['fps'])
                if not 0 < fps < 10000:
                    raise ValueError('Invalid explicit fps')
                time_fields = dict(clock_domain='FILE_FPS:' + session_id,
                    source_time_s=(sequence - rows[0]['sequence']) / fps, quality='FPS_DERIVED')
            raw = ply.read_ply_bytes(path)
            observation = ply.ply_frame(path, raw_bytes=raw, sensor_id=document['sensor_id'], session_id=session_id,
                sequence=sequence, stream=document.get('stream', 'UNKNOWN'),
                source_kind=source, time=schema.FrameTime(**time_fields))
            sink.append(observation, row['replay_time_s'], original_ply=raw)
        sink.close()
    except Exception as exc:
        sink.fail(str(exc))
        raise
    return sink.rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=Path, help='Explicit JSON frame index')
    parser.add_argument('output', type=Path, help='New recording directory')
    args = parser.parse_args()
    print(json.dumps(dict(frames=import_index(args.index, args.output), recording=str(args.output))))


if __name__ == '__main__':
    main()

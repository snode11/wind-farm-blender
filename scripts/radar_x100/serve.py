"""Publish decoded RadarFrame objects through the isolated loopback radar channel."""
from pathlib import Path
import argparse
import importlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.radar_x100 import acquisition, recording, schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--adapter', help='Trusted local module:factory returning an iterator of decoded RadarFrame')
    source.add_argument('--replay', type=Path, help='Software channel check; preserves recorded origin, not live hardware')
    parser.add_argument('--port', type=int, default=9877)
    parser.add_argument('--record', type=Path, help='New durable recording directory')
    parser.add_argument('--record-capacity', type=int, default=32)
    args = parser.parse_args()
    if not 1 <= args.record_capacity <= 1024:
        parser.error('record-capacity must be 1..1024')
    sink = recording.Recorder(args.record) if args.record else None
    fanout = acquisition.Fanout(sink, record_capacity=args.record_capacity)
    publisher = None
    try:
        publisher = acquisition.LocalPublisher(fanout, args.port)
        print(json.dumps(dict(channel='x100-frame.v1', host='127.0.0.1', port=publisher.port,
                              input='REPLAY_CHANNEL_CHECK' if args.replay else 'EXTERNAL_ADAPTER')), flush=True)
        start = time.monotonic()
        if args.replay:
            replay = recording.Replay(args.replay)
            def frames():
                for index, t in enumerate(replay.times):
                    delay = t - (time.monotonic() - start)
                    if delay > 0:
                        time.sleep(delay)
                    yield replay.frame(index)
            iterator = frames()
        else:
            module, factory = args.adapter.split(':', 1)
            iterator = getattr(importlib.import_module(module), factory)()
        for frame in iterator:
            if not isinstance(frame, schema.RadarFrame):
                raise TypeError('Adapter must yield canonical RadarFrame instances')
            fanout.submit(frame, time.monotonic() - start)
        time.sleep(.25)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        fanout.failure = 'Acquisition failed: ' + str(exc)
        raise
    finally:
        if publisher:
            publisher.close()
        fanout.close()
        print(json.dumps(dict(display_replacements=fanout.display_drops,
            recording_state=sink.state if sink else 'DISABLED', recording_error=sink.error if sink else None)), flush=True)


if __name__ == '__main__':
    main()

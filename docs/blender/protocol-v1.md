# WFRL Protocol v1

Part 3 freezes the local frontend contract and tests it against a local protocol peer. It does **not** provide the Part 4 Bridge/Trainer/FLORIS/FAST.Farm integration or claim backend control has occurred. Listening services must bind loopback. This protocol has no authentication or remote-network security contract.

## Framing and implementation

Each message is `uint32_be(length) || UTF-8 JSON object`. Length excludes the four-byte header and must be 1–1,048,576 bytes. Zero, oversized lengths, malformed UTF-8/JSON, duplicate object keys, nonfinite numbers, nesting beyond 64, unsupported versions and invalid schemas are fatal protocol errors. A failed decoder must be discarded; its connection must close. EOF with a partial frame is a transport disconnect, never a complete message. Lengths are checked before allocating a frame body. JSON must not contain NaN/Infinity, even inside command arguments. Unknown object fields are forward-compatible; unknown message types are rejected.

The canonical standard-library implementation is `wfrl/blender_bridge/messages.py`. Development frontend `protocol.py` loads that file without importing the backend package. The extension build replaces the loader with the canonical source, so the installed extension needs no repository, backend package or nonstandard dependencies. `encode_message`, `validate_message`, `FrameDecoder.feed`, `SessionSequenceGuard`, `normalize_rotor_speed`, `data_age_seconds`, and `channel_is_stale` raise `ProtocolError` for contract violations. Strings and object keys must be valid UTF-8 without lone surrogate code points. Frame decoding validates structure; the transport additionally owns session, direction, handshake and sequence enforcement.

## Envelope and handshake

Every message has these fields:

```json
{"protocol_version":1,"type":"command","session_id":"opaque-session-id","sequence":1,"payload":{"command":"session.sync","arguments":{}}}
```

`sequence` is an integer from 0 through 2^53−1, never a boolean. It increases strictly per **session and sending direction**. Gaps are allowed when replaceable telemetry is coalesced; duplicate or reversed numbers are rejected. Reliable commands, lifecycle, safety and error events must never be silently dropped. Queue limits and work budgets belong to transport; overflow must explicitly fail the connection.

The first frontend message on every connection is:

```json
{"protocol_version":1,"type":"hello","session_id":"","sequence":0,"payload":{"supported_versions":[1],"capabilities":[],"resume_session_id":null}}
```

Only `hello` permits an empty session. The server allocates a nonempty opaque session and replies:

```json
{"protocol_version":1,"type":"hello_ack","session_id":"server-session","sequence":0,"payload":{"selected_version":1,"capabilities":["pause","single_step"],"run_status":"READY","mode":"interactive_training","resumed":false}}
```

Capabilities are nonempty string names. `pause` and `single_step` are optional control capabilities; their presence does not bypass mode/run-state restrictions. Part 3 rejects binary capability names. `CONNECTED` is established only after a valid acknowledgment. A pre-session incompatible version may be reported by closing the socket; the client reports the handshake failure. Error envelopes require an assigned nonempty session.

On reconnect, `hello.payload.resume_session_id` is the previous session ID. A resumed acknowledgment must use that ID with `resumed:true` and a sequence strictly newer than the previous inbound sequence. Preserve both directions' sequence counters across reconnect. A new session uses a different ID and `resumed:false`; only then reset counters. Reject unsolicited resumed sessions, an old ID masquerading as a new session, and old-session data. Handshake provides confirmed run state and mode; transport validates the handshake before admitting subsequent data. Do not replay unacknowledged commands automatically. Disconnection changes connection status and marks data/state unconfirmed, without inventing STOPPED or backend termination.

## Payload schemas

All examples below describe `payload`; they still require the complete envelope. Fields shown are required unless explicitly optional. Business modes are `demo`, `interactive_training`, `formal_training`, `replay`. Run states are `READY`, `STARTING`, `RUNNING`, `PAUSED`, `DRAINING`, `STOPPED`, `FAILED`.

| Type | Payload |
| --- | --- |
| `command` | `command`: one of `scene.load`, `run.start`, `run.pause`, `run.resume`, `run.step`, `run.stop`, `run.reset`, `session.sync`, `channel.set`; `arguments`: JSON object. Backend-specific argument semantics remain Part 4 and must be validated there before execution. |
| `lifecycle` | `run_status`: state enum; `reason`: null or nonempty string; `mode`: current business mode; `capabilities`: current nonempty capability names. State transitions are validated by the receiving state model. Commands do not themselves establish lifecycle state. Mode and capabilities are repeated because `run.start` may change both after the initial handshake, including formal training that produces no Snapshot. |
| `snapshot` | Data context below, `turbines`: array of `{turbine_id, channels}`, `farm`: channel map. Turbine IDs are nonempty stable strings and unique within the frame. `farm` has no invented turbine ID. |
| `curve` | Data context, `channel`: canonical channel name, `turbine_id`: string or null for farm scope, `data`: channel record whose value is an array of finite `[time,value]` points or null. Point time uses the context timestamp timebase. |
| `wake` | Data context, `encoding`: `json`, `turbine_ids`: ordered ID list describing array order, `data`: channel record. Shape/grid metadata may be additional fields; physical grid semantics/rendering remain Part 5. |
| `safety_event` | Data context, `event_id`: nonempty string, `severity`: `info`, `warning` or `critical`, `message`: nonempty string, `turbine_id`: string or null for farm scope, `data`: channel record describing event evidence. |
| `error` | `code`: nonempty string, `message`: nonempty string, `fatal`: boolean. Fatal errors close the connection; an error does not independently confirm backend termination. |

Every snapshot, curve, wake and safety event includes data context:

```json
{"mode":"interactive_training","step":12,"timestamp":{"value":1.2,"timebase":"simulation_seconds"}}
```

`step` is a nonnegative safe JSON integer. Timestamp value must be finite; timebase is `simulation_seconds`, `unix_seconds`, or `replay_seconds`. Timestamp is source time, never a receiver freshness clock. Future sensor messages must use the same per-channel records and context; no separate sensor message type is negotiated in Part 3.

## Channel records and data truth

A snapshot `channels`/`farm` map associates canonical channel names with records:

```json
{"rotor_speed":{"value":12.1,"unit":"rpm","validity":"valid","error":null,"fidelity":"DIRECT","provenance":{"backend_session":"backend-run-id","channel":"RotSpeed"},"source_age_seconds":0,"stale_after_seconds":2}}
```

Every record requires all eight fields shown. Known scalar numeric telemetry channels (`rotor_speed`, `power`, `yaw`, `pitch`, `wind_speed`, `wind_direction`, `torque`, `reward`) require finite non-boolean numbers when non-null. Other channels may contain arrays or other JSON data appropriate to the channel; their physical contracts remain backend adapter responsibilities. Curve points require finite numeric coordinates, and rotor speed curves also require `rpm`. Missing/nonfinite source readings become `value:null`, `validity:"invalid"` and a nonempty reason; the codec rejects nonfinite wire values and does not silently replace them with zero.

| Validity | Value/error semantics |
| --- | --- |
| `unsupported` | null; nonempty explanation of unsupported channel |
| `waiting` | null; waiting for first frame reason |
| `valid` | non-null value; null error |
| `stale` | last finite JSON value or null; nonempty stale reason |
| `invalid` | null; nonempty validation/error reason |

Fidelity is per field/channel: `DIRECT` requires provenance `backend_session` and `channel`; `EXPORTED` requires `file` and `channel`; `SYNTH` requires `formula`. Additional provenance fields may identify exporter versions, original backend sessions or formula parameters. Mixed-source frames retain each channel's metadata; a single outer fidelity label cannot replace it. A file path is provenance, not an instruction to open it.

Canonical rotation channel is `rotor_speed`, with unit `rpm`. Backend adapters may call `normalize_rotor_speed(mapping)` on legacy `rotorspeed` input. Both names with equal values collapse to one canonical key; conflicting values raise `ProtocolError`. Wire channel maps reject `rotorspeed`. The helper does not mutate input or rename backend physical channels.

Freshness is `source_age_seconds + (now_monotonic − received_monotonic)`. Both source age and threshold are finite nonnegative seconds. Freeze thresholds by v1 channel family: snapshot telemetry (including future sensor channels) and curves **2 s**, wake **5 s**, safety event evidence **10 s**. Records must carry the matching threshold; arbitrary sender overrides are rejected. A channel becomes stale at age ≥ threshold, or immediately when marked stale. This is data evidence age; a safety event remains in history after its evidence expires. Simulation and replay timestamps must never be subtracted from wall time. Changing freshness policy requires a protocol revision or an explicitly negotiated future channel definition.

## Reserved binary wake extension (not implemented)

`type:"wake_binary"` and any binary capability are explicitly rejected in Part 3, before any binary codec dispatch. Do not feed raw binary payloads to the JSON decoder. A future negotiated capability `wake_binary_v1` must introduce a separate tagged outer frame (magic `WFRB`, version byte, metadata length, body length) selected only after negotiation, with a UTF-8 JSON metadata object specifying `type`, session/sequence, data context, fidelity/provenance/validity, ordered turbine IDs, shape, dtype, endian, compression and both compressed/uncompressed lengths. Proposed maxima: metadata 64 KiB, encoded body 16 MiB, decompressed body 64 MiB; decompression must stop at the advertised bound and reject mismatch. No such tagged frame is accepted by this v1 JSON implementation. Part 5 must finalize and test binary layout/version negotiation before enabling it.

## Verification

`python3 -m unittest discover -s tests/blender_bridge -p test_messages.py` covers fragmented/coalesced frames, bad UTF-8/JSON and duplicate keys, limits, envelope/version/type errors, schemas, session sequence replay, provenance, mixed fidelity, invalid/missing/stale values, rotor aliases and monotonic age. Initial failing and passing runs are recorded under `evidence/part3/protocol-red.txt` and `protocol-green.txt`. Transport tests separately exercise handshake ordering, reconnect, timeout, partial sending and disconnect behavior.


## Negotiated training statistics extension (`training_stats_v1`)

`training_stats` is a server-to-client JSON message in Protocol v1. The client
lists `training_stats_v1` in `hello.capabilities`; the server acknowledges it only
when both peers support it. This extension support is independent of the current
business mode, so a Demo connection may negotiate before starting formal training.
The server stores the intersection for each socket and suppresses statistics when
it is absent. Existing control capabilities (`pause`, `single_step`) retain their
mode-dependent advertisement semantics. Lifecycle messages preserve the negotiated
extension and cannot introduce it on an unnegotiated connection. The client rejects
unrequested acknowledgment extensions, unnegotiated statistics and lifecycle
extension injection before returning messages to application state. Clients send
commands only; statistics are never accepted in the reverse direction.

Disconnect clears negotiated extensions. Reconnect performs a new intersection,
even when resuming the same session; the existing session ID and monotonically
increasing directional sequence rules remain in force. Training records use that
same server sequence stream, not an independent sequence counter. Optional
`run_id` in `hello_ack` and `lifecycle` is null (no run) or a nonempty string.

A `training_stats` payload requires:

| Field | Contract |
| --- | --- |
| `run_id` | Nonempty ID of one run, distinct from the protocol session. |
| `record_kind` | `progress` or `iteration_stats`. |
| `mode` | Existing business-mode enum. |
| `step`, `agent_step`, `iteration` | Nonnegative safe integers; respectively cumulative control steps, cumulative agent transitions, global training iteration. |
| `episode` | Optional nonnegative safe integer, supplied only with confirmed global meaning. |
| `timestamp` | `{value: finite number, timebase: "unix_seconds"}`; actual record production time. |
| `phase` | `waiting`, `warmup`, `sampling`, `updating`, or `done`. Failure and user stop use lifecycle state, not fabricated `done` progress. |
| `source_age_seconds` | Finite nonnegative age since production, including file waiting time before forwarding. |
| `stale_after_seconds` | Exactly 30 seconds. |
| `stats` | Channel-record map; empty for progress. |

An `iteration_stats` record includes `mean_power` (MW, average wind-farm total
power over the iteration's control steps), `mean_reward` (normalized training
reward, `B_rew.mean()`), `value_loss` (actual optimization minibatch `v_log` mean),
`explained_variance` (actual `ev`), `episode_return`, and `learning_rate`.
Additional named scalar statistics are allowed; raw reward must use a distinct
name such as `mean_raw_reward`. Every channel independently includes `value`,
`unit`, `validity`, `error`, `fidelity`, `provenance`, `source_age_seconds`, and
`stale_after_seconds`, using the ordinary channel metadata contract except for a
fixed 30-second threshold. Non-null values must be finite numeric scalars (not
booleans). Unavailable statistics use null and an explicit unsupported/waiting/
invalid reason; no statistic is inferred from instantaneous telemetry.

Record freshness and each statistic's freshness are independent. Receivers add
local monotonic elapsed time to the supplied source age; at 30 seconds the data
is stale. A new progress event updates only the phase/progress age and never
refreshes older statistic channels. Long iterations may naturally leave the
previous iteration stale. Existing snapshot/curve 2-second policies are unchanged.
`validate_training_stats_payload` exposes this payload contract for JSONL readers.

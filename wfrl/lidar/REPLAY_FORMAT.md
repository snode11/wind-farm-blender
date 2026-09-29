# Lidar replay formats

The original replay 1.0 contract below describes the legacy B2 / normal-close path. It remains supported and is not the contract for the dual-beam overlay bundled in 0.3.12. The newer implementation is in the matching local source; the public source archive has not been synchronized with the ZIP-only releases.

## Dual-beam review v1

`wfrl.dual-beam-review.v1` is a review overlay on `wfrl.farm-flex-review.v3` geometry. The manifest uses `status: REVIEW_ONLY`, `sample_kind: source`, a source directory in `source_package`, complete `source_hashes`, a result `files` inventory, and the source segment and turbine IDs. `performance_status: PENDING_ACCEPTANCE` is separate from software package validation.

`dual_beam_replay.resolve_package(path)` verifies source and result hashes, the complete source inventory including its manifest, required `results.json` and `calibration.json`, supported calibration/reconstruction method, and matching turbine IDs and segment. Rejected installations must not load. The supported research reconstruction method is `hub-axis.v1`; verification of a package is not accuracy acceptance.

Samples contain `observations` for S1/S2/S3, a reconstruction, evaluation fields and independent S1 observation state. S2/S3 must pair at one saved time on the same blade. S1 valid blade hits drive their own alarm regardless of pair validity. Saved samples and precomputed prefix states drive replay; interpolation of displayed geometry cannot create a missing measurement. Seeking and replaying must not double-count history or reveal future event endpoints. Invalid/expired measurements remain missing, not zero or a declaration of safety.

`dual_beam_package.portable_copy(package, output)` requires a new output directory, copies the verified source inventory into `source/`, copies result payloads and writes `source_package: source`, `portable: true`, then resolves the new package again. Transfer the entire directory, not only `manifest.json`. The self-contained source copy removes dependency on the developer's source location.

The installed layout is 10°/12°/14°; 40 Hz saved data and ideal geometric hits do not establish hardware sampling performance, field accuracy or protection latency. See the [release and validation scope](../../docs/blender/发布状态与验证范围.md).

## Legacy replay 1.0

`replay.py` and its sibling `evidence.py` use only the standard library and must be vendored together. Public API:

- `publish_package(path, manifest, motion, measurements, report)` writes a new directory; existing directories are never overwritten.
- `ReplayPackage.load(path)` verifies hashes, provenance declarations, values, and recomputed cumulative states. Errors are `ValueError` with the cause.
- `ReplayReader(package).at(simulation_time_s)` is deterministic and does not use wall time. `start_s` / `end_s` use the original simulation clock. It clamps outside seeks to the segment interval.

Files: `manifest.json`, `motion.json`, `measurements.json`, `cumulative.json`, `statistics.json`, `report.md`. Manifest `files` maps the five payload filenames to SHA-256. Raw sources are external archive references with SHA-256; playback does not require those archives mounted. Hashes detect corruption, not authenticity. Evidence flags must be populated from actual validation, not inferred from the simulator name.

Manifest fields and validation are defined in `_validate`. `source` must be `FAST.Farm`; `status` must be `READY`. Required provenance includes run/turbine/model/controller, flexible reconstruction and tower assumption, calibration, algorithm and postprocessor versions, original and segment time ranges, selection basis, raw sources and numerical validation. `motion_azimuth: unwrapped_deg` declares continuous rotor phase (359 then 361, never 359 then 1). Nacelle position and orientation arrays must be present, and the producer documents orientation axis conventions in `coordinate_system`/calibration.

New publication requires `validation.evidence_contract: numerical-comparison-v2`, embedded `numerical_evidence`, `analytic_verified`, `spatial_comparison_completed`, `temporal_comparison_completed`, and `collision_excluded`. The embedded evidence includes finite analytic metrics, spatial and temporal run/grid linkage, sample counts, full-grid sampling statistics and passage details, plus the empirical numerical difference. Its sampling interval must match the package segment. `convergence_assessment: NOT_ASSESSED_NO_TOLERANCE` explicitly means no convergence acceptance tolerance was supplied; completed comparisons do not establish accuracy acceptance. New packages must not carry the old `*_convergence_verified` flags. Schema remains 1.0 because payload/replay semantics are unchanged.

The loader remains compatible with archived 1.0 packages containing the original evidence flags. The producer refuses those flags alone for new publication. Structural package checks do not authenticate evidence. The physical producer additionally verifies run configs/completion and comparison linkage, recomputes the inexpensive analytic checks and full-grid sampling statistics, and binds all three runs' configuration, status, solver log and processed-data hashes. It upgrades raw measurements only in memory and derives the report window/rate from configuration; archived raw files and previous packages are not rewritten.

Motion records: `time_s`, `azimuth_deg`, `yaw_deg`, `pitch_deg` (three blades), `rotor_speed_rpm`, `nacelle_position_m`, `nacelle_orientation_deg`. Motion spans the whole segment, with unique increasing times. Reader linearly interpolates continuous azimuth, pitch, and RPM; yaw takes the shortest circular path. Additional geometry fields are held from the preceding record.

Measurements: `time_s`, `blade_id` (1–3), `expected`, `passage_id`, `truth_m`, `truth_tip_point_m`, `truth_wall_point_m`, `beams` keyed `B1`, `B2`, `B3`. Each beam has boolean `valid`, `slant_range_m`, `estimate_m`, `error_m`, `hit_point_m`, `reason`. Valid estimates require predeclared expected evaluation samples, finite values, geometry audit endpoints, and `error_m = estimate_m - truth_m`. Invalid estimates/errors are null and require a reason. Extra audit fields are preserved.

B2 alone drives the card and main coverage. Statistics use expected samples as denominator, with missed observations included; empty denominators and empty errors are null. P95 is exact nearest rank `ceil(.95*n)`. Maximum positive bias is `max(0, max(errors))`. Passage counts use predeclared passage IDs; cumulative missed passages mean passages without any valid sample up to that time (a later hit can reduce this count).

Expiry is `min(max_hold_s, 20 / abs(rpm) * passage_margin)` seconds for a three-blade rotor; zero RPM uses finite `max_hold_s`. The RPM is from the preceding motion sample at the measurement time. Threshold hysteresis is precomputed from B2 estimates; after expiration it resets. Seeking reads the saved state and cumulative statistics at or before the target time. The retained measurement always contains paired truth, estimate, and error from one timestamp; it becomes null after expiry. Age remains available for diagnostic display. A package with no valid B2 cannot publish READY.

Publication writes a temporary sibling directory, validates it, then renames. Failures during writing/validation preserve the temporary directory plus `failure.json`. Validation failures before writing raise directly, leaving caller input and raw outputs untouched.

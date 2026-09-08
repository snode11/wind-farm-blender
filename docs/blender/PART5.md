# Blender Part 5

Part 5 adds the visual/data layer for complete 3D presentation while keeping
control and simulation state in Part 4.  The Blender extension stays usable
without NumPy, FLORIS, MPI, or FAST.Farm installed.

## Delivered

- `wfrl_blender/wake.py` validates JSON wake frames, labels FLORIS proxy data
  as `SYNTH`, labels FAST.Farm DisXY data as `EXPORTED`, converts DisXY grids to
  reusable mesh topology, and coalesces stale replaceable frames.
- Demo proxy curves update in place from a deterministic phase.  The update
  path does not create Blender objects per frame.
- `wfrl_blender/sensors.py` defines deterministic Lidar beams and a geometric
  camera frustum.  They are presentation geometry, not optical measurements.
- `wfrl_blender/atmosphere.py` provides `clear`, `overcast`, and `dusk` visual
  presets.  The atmosphere toggle and quality setting only change display
  metadata/nodes; they do not alter backend inputs or rewards.
- `wfrl_blender/overlays.py` formats status, time, wind, backend, power, wake,
  sensor, provenance, and fidelity labels for presentation UI.
- Camera selection supports world, top, side, T1 close-up, and T1 sensor views.
  Yaw, local blade pitch, RPM integration, pause, and manual-pose priority are
  covered by exact unit tests.
- The Views panel exposes PNG still rendering and frame-range animation
  rendering from the active WFRL camera.

## Verification

The Part 5 pure-Python suite covers payload bounds, fidelity/provenance,
DisXY point ordering, frame coalescing, Lidar geometry, atmosphere metadata,
overlay labels, and animation math.  The Blender smoke
`tests/blender/part5_visual_smoke.py` verifies that 600 proxy updates keep the
object count stable, DisXY updates reuse the same mesh datablock, and camera,
sensor, and atmosphere controls are available in a built scene.

## Fidelity boundary

The current Bridge v1 protocol accepts JSON wake payloads only.  Binary wake
frames remain deliberately rejected until a separately negotiated and bounded
codec is specified.  No current Part 5 visual layer claims that a FLORIS proxy
is a measured flow field, and no FAST.Farm DisXY frame is fed into control or
reward calculations by the Blender extension.

## Presentation revision

The Demo renders sixteen longitudinal tracers and twenty-two advecting rings
per turbine. Both builders and animation use hub-local coordinates, with the
hub offset applied only on the object. Wake geometry has no shadow visibility;
it is a scientific overlay rather than physical wire geometry. Ground grids,
inflow arrows and sensor helpers are hidden by default; sensors remain togglable.

The scene uses a perspective camera, meter-scale grass/soil variations,
photographic ground maps, roughness/bump, a packed HDR sky, distant ridges,
service roads conforming to the terrain mesh, maintenance pads and linked
shrub/rock instances. Terrain remains presentation-only. Asset attribution is
in `blender_frontend/wfrl_blender/assets/landscape/SOURCES.md`.

- `scripts/blender/render_part5.py`: 1080p Cycles / 128 samples, packed
  `evidence/part5_presentation.blend` and `evidence/part5_presentation_preview.png`.
- `scripts/blender/render_part5_motion.py`: 36 motion preview frames and a
  verified MP4. A normal run re-renders all frames; `--resume` after Blender's
  `--` separator continues an interrupted render without accepting incomplete
  PNGs.
- `tests/blender/wake_placement_smoke.py`: hub alignment through frames/phases,
  ground clearance, ring topology, downstream direction, layer toggles.
- `tests/blender/part5_visual_smoke.py`: stable object count across 600 updates
  and DisXY mesh reuse. This is a lifecycle check, not a frame-rate benchmark.

Realtime Demo uses Eevee; the saved presentation uses Cycles for still quality.
The scene is an improved scientific presentation, not a certified cinematic or
photoreal final. Long-duration performance has not been benchmarked with the
new environment instances.

## Client-facing flow cues

The wake retains 16 longitudinal traces and 22 rings per turbine, plus 12 short
bright advection tracers. A shared bounded centerline moves by at most 4 m
laterally and 2 m vertically. The radius expands downstream with a small smooth
modulation. Inlet alpha ramps in over 12 m; downstream alpha fades to zero over
the last 217 m. All geometry is reused during animation and casts no shadows.

Demo phase advances continuously at 0.9 ring spacings per second rather than
resetting to zero at every spacing. These are SYNTH presentation cues, not wind
velocity samples. The motion preview uses the production frame-change handler,
not a separate animation path: 36 samples two timeline frames apart, played at
12 fps, preserve approximately real-time Demo motion.

The motion set was completed on 2026-09-07 using Blender 5.2.1. With no prior
source manifest available, the script re-rendered all 36 frames from
`evidence/part5_presentation.blend`, then produced
`evidence/part5_presentation_motion.mp4` and
`evidence/part5_presentation_frames/render_manifest.json`. The render script's
PNG and encoded frame-count checks passed. This is a 960×540, 12 fps,
3-second motion preview. For subsequent changes, inspect current output state
with `python3 scripts/blender/check_part5_motion.py` rather than relying on
filenames; the manifest records the source blend hash.

## Grass patches and background layers

Vegetation and small rocks use seeded Gaussian clusters rather than uniform
scatter. The road corridor is kept clear; higher ground carries fewer shrubs
and a mixture of dry grass. Grass and rocks sit on the actual terrain mesh via
ray casts. A continuous distant ground closes the gap beyond the local terrain. Three distant ridges add overlapping silhouettes with progressively
muted color. The camera far plane is extended to avoid clipping these ridges.
No solar disk or sun prop has been added.

## Modelling detail revision — 2026-09-07

User priorities: mechanical fittings, blade section correspondence, road/base
transitions, then ridges and vegetation. Wake geometry, density, materials and
animation are unchanged.

- `mechanical_details.py` adds root flanges/seals, a yaw skirt/seal, tower weld
  lines, a service door with hinges/handle, steps/rails, a roof gasket and side
  louvres. Fittings use the existing tower/yaw/pitch parents and are explicitly
  illustrative; they do not add engineering dimensions to the source asset.
- Blade contours now share leading/trailing-edge landmarks and cosine-spaced
  chord positions on both surfaces. The single trailing-edge seam uses the
  midpoint of the source endpoints. Source station spans, chords, twist and
  backend aerodynamics are unchanged. The closed loft retains 7,008 vertices;
  root/tip bounds and independent pitch remain verified.
- Roads have graded shoulders, camber and compacted tracks. Maintenance pads
  have clipped corners and a terrain-sampled apron. The concrete plinth extends
  below ground and has a softened, higher-resolution rim.
- Shrubs use three linked leafy prototypes with stems; grass uses five curved,
  tapered blades per tuft. Cover-patch annulus winding is corrected and the
  ground sampling is denser to reduce intersections with terrain triangles.
- Distant ridges have finer silhouettes, smooth foothills and less fine detail
  in distant layers. Terrain is still presentation-only and uses the existing
  three-turbine demo corridor.

Rebuild review artifacts with `scripts/blender/render_part5_details.py`.
It produces `evidence/part5_details/part5_details.blend` and four 1440×810
Cycles/32-sample stills: overview, nacelle, foundation and vegetation. Older
Part 5 artifacts are retained. The review script explicitly preserves the
existing line-only wake render convention after UI frame synchronization.
The added vegetation geometry has not been benchmarked for sustained realtime
frame rate; linked prototypes and one batched grass mesh limit object overhead.

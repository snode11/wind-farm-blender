# Landscape and inflow-driven cinematic update

Completed scope: simplify distant shrubs, vary illustrative flow streaks, drive live cinematic animation with validated backend inflow and simulation timestamps. Turbine geometry unchanged (57,230 base vertices per turbine); V150 migration paused pending manager confirmation.

## Vegetation

Shrubs farther than 180 m horizontally from the three demo turbine locations use cached reduced meshes. Nearby shrubs retain original leaves. Positions, object count, materials and grass remain unchanged. This is static detail zoning, not camera-distance LOD; inspecting a remote shrub closely will expose its simpler geometry.

Object-summed shrub vertices: 1,498,464 -> 720,852 (-51.9%). 1,892 shrubs. Full scene before 2,098,990 -> approximately 1,321,378 base vertices. Turbine mesh count/vertices compared exactly before/after and unchanged. Reapplying optimization is idempotent.

At 1100x700, EEVEE 16 samples, alternating three warmed renders of the same frame: median 0.44067 s -> 0.43110 s (~2.2%). First render excluded because it includes warmup. Paired interactive viewport runs of the same 55 frames, first five discarded: mean 11.37 -> 11.77 frames/s (~3.5%); median frame time 85.06 -> 84.28 ms. These are short local measurements, not a claim of proportional speedup with vertex reduction. The earlier playback.json ran with a small side viewport and is superseded by viewport-playback.json.

## Cinematic

72 strands per turbine retained; moving streak length range 42–154 m, decorrelated starting positions and downstream curl phases. Opaque emission and stable persistent trail widths retained. Backend live snapshots take precedence over local FRONT/RANDOM controls and use meteorological wind direction, wind speed and simulation time. Bounded history preserves inflow changes; travelled distance is accumulated to avoid multiplying the newest speed by the entire elapsed time. Snapshot pause freezes the frame; zero wind hides the curves; rewind starts a new history. Invalid inflow retains the last picture and reports waiting. No inferred CFD field: trails remain SYNTH. DisXY export is still handled separately.

## Validation

154 frontend Python tests passed. Five Blender cinematic tests passed. verify.py covers geometry invariance, repeat optimization, backend heading, snapshot time, step, calm wind and reset. before.png and after.png are actual matched EEVEE renders. Final scene is review.blend. Installed extension updated, ZIP rebuilt. Source for reproducing checks and raw measurements is in this directory.

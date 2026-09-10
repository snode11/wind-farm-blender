# Blade attachment audit and rounded tip — 2026-09-08

Order: root/motion audit completed first, then tip geometry revised.

## Attachment and motion audit

Blender 5.2.1, rigid Blade1, pitches -5/0/2/25/45/70/90 degrees,
rotor azimuth 0–355 degrees in 5-degree increments. Other blades share
this geometry with 120-degree phase offsets. Minimum sampled vertex radial
clearance to the tapered tower: 1.504994 m at pitch 2°, azimuth 180°.
This is a vertex sampling result, not a certified surface-distance bound,
continuous collision proof, or an elastic deflection clearance assessment.

Evaluated mesh BVH checks detect surface intersections with RootFairing,
RootFlange, PitchSeal, Hub and Spinner at every sampled pitch. These are
existing overlapping presentation solids, not a clean mechanical assembly.
Intersection counts are triangle-pair counts, not penetration depths.
Root geometry was not changed. Surface intersections alone do not establish
whether every seam is visually covered; absence of visible gaps is not certified.
Exact results and reproducible Blender script are stored alongside this note.

## Tip finish

Replaced smoothstep collapse (cusp) with sqrt(1-t^3) radial scaling:
zero first/second taper derivatives at the shoulder, rounded pole near the
original tip extent. 24 end-section rings cluster toward the pole to reduce
silhouette faceting. Original aerodynamic asset and earlier station sections
remain unchanged. This remains an illustrative presentation tip.

Validation: 7 blade tests passed; Blender geometry/independent pitch smoke
passed; fresh tip render visually inspected; git diff --check passed.

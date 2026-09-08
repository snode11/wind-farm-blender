# Environment revision — 2026-09-08

- Shared ground/ridge palette with smooth distance haze replaces layer-specific solid colors.
- Broad and medium-scale ground variation; no additional geometry or vegetation.
- World overview: location (-330, -780, 245), target (540, 0, 105), lens 28.5 mm.
- Wake geometry/materials and turbine materials remain unchanged.

Validation: 13 targeted Python tests passed in the wfrl-mac environment.
The lightweight preview checked all three rotor envelopes against 2% frame margins.
The final 960×540 preview was visually inspected. This is a low-sample visual check,
not a realtime performance measurement. Previous detail renders and saved blends
are retained; the preview uses existing geometry plus current shader/camera code.

Fresh scene regeneration also passed the existing native Blender camera framing
smoke for World, Top and Side, including the staggered three-turbine layout.

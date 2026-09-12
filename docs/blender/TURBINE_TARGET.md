# Fixed turbine target

User update, 2026-09-11: Vestas V150 4.0/4.2 MW conversion is PAUSED pending confirmation with the user’s manager. Keep current NREL 5 MW geometry and approximately 57,230 base vertices per turbine unchanged. The specifications below are reference only.

Official reference: https://www.vestas.com/en/energy-solutions/onshore-wind-turbines/4-mw-platform/V150-4-2-MW

- Rated power: 4,000/4,200 kW.
- Rotor diameter: 150 m (not tower height).
- Blade length: 73.7 m; maximum chord: 4.2 m.
- Nacelle: 12.8 m long, 4.2 m wide, 3.4 m transport height, 6.9 m installed height including CoolerTop.
- Published hub heights: 105, 123, 145, 155, 166 m; site configuration remains to be selected.

Current Blender geometry remains based on the packaged NREL 5 MW geometry. This decision does not relabel those meshes as a V150 or replace the backend turbine/power/structural models. A V150 migration must update geometric dimensions and dependent camera/wake envelopes consistently. Proprietary airfoil and structural data are not provided by the product page; distinguish any illustrative reconstruction from validated engineering data.

"""Author the commercial-style root-kink AeroDyn blade definition.

The planform keeps the NREL 5MW station radii, prebend, sweep and twist so the
saved 19-station MAPPO transforms stay applicable and the existing airfoil
polars remain valid under chord normalisation. Only chord and airfoil
assignment change, which is what produces the visible root shoulder.

Design intent, measured against the NREL 5MW baseline:
  - only the r=1.5 m bolt circle stays a true cylinder, and it sits deep inside
    the 2.40 m spinner; the first station that emerges (r=2.87 m) is already a
    DU40 aerofoil, so no bare cylinder stub protrudes the way the NREL root does
    (NREL keeps Cylinder1/Cylinder2 out to r=6.8 m)
  - max chord moved inboard 15.85 -> 11.75 m (r/R 0.25 -> 0.19)
  - trailing edge accelerates into the shoulder (+0.208 then +0.250 m/m) and
    reverses to -0.065 m/m, a 0.316 slope jump against the baseline's 0.110.
    The acceleration matters: a decelerating approach makes the monotone
    Hermite limiter round the apex into a bulge instead of a corner.
  - airfoil sections brought inboard one step so a real profile, not a
    cylinder, carries the shoulder

This is a synthetic commercial-style planform, not manufacturer CAD and not a
calibrated match to any photographed machine. Structural properties and
airfoil polars are inherited from the NREL 5MW baseline and were NOT re-derived
for this planform.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "projects/nrel5mw-reference/template/5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat"
TARGET = ROOT / "projects/commercial-rootkink-reference/template/5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat"

HUB_RAD = 1.5

# (chord, airfoil_id) per station, in baseline row order. Station radii,
# prebend, sweep and twist are read from the baseline file so they cannot drift.
PLANFORM = [
    # chord  afid   approx r = span + 1.5
    (2.300,  1),  #  1.50  structural bolt circle, buried deep inside the 2.40 m spinner
    (2.900,  3),  #  2.87  emerges from the spinner already shaped as a DU40 aerofoil
    (3.700,  3),  #  5.60  still rising; held below the apex so the trailing edge
                  #        accelerates into the corner at r=8.33 (sharp kink)
    (4.850,  4),  #  8.33  MAX CHORD - shoulder apex; kink ~6.8 m from the hub,
                  #        between the old r=11.75 (too far) and r=5.60 (too near)
    (4.600,  4),  # 11.75  gentle fall begins
    (4.300,  5),  # 15.85
    (4.000,  5),  # 19.95
    (3.720,  5),  # 24.05
    (3.460,  6),  # 28.15
    (3.220,  6),  # 32.25
    (2.990,  7),  # 36.35
    (2.760,  7),  # 40.45
    (2.540,  8),  # 44.55
    (2.400,  8),  # 48.65
    (2.270,  8),  # 52.75
    (2.150,  8),  # 56.17
    (2.000,  8),  # 58.90
    (1.419,  8),  # 61.63
    (1.419,  8),  # 63.00  tip
]


def _fortran(value):
    """AeroDyn-style 7-digit mantissa exponential, matching the baseline file."""
    return f"{value:.7E}"


def build(source=SOURCE, target=TARGET):
    lines = source.read_text(encoding="latin-1").splitlines()
    header_index = next(i for i, line in enumerate(lines) if "BlSpn" in line)
    # Rows start after the name row and the unit row.
    first_row = header_index + 2
    rows = lines[first_row:first_row + len(PLANFORM)]
    if len(rows) != len(PLANFORM):
        raise ValueError(f"Baseline has {len(rows)} station rows, planform has {len(PLANFORM)}")

    rebuilt = []
    spans = []
    for row, (chord, afid) in zip(rows, PLANFORM):
        fields = row.split()
        if len(fields) < 10:
            raise ValueError(f"Unexpected station row: {row!r}")
        spans.append(float(fields[0].replace("D", "E")))
        # Keep BlSpn, BlCrvAC, BlSwpAC, BlCrvAng, BlTwist; replace BlChord and
        # BlAFID; keep the trailing BlCb/BlCenBn/BlCenBt columns.
        rebuilt.append(
            f"{fields[0]}  {fields[1]}  {fields[2]} {fields[3]}  {fields[4]}  "
            f"{_fortran(chord)}        {afid}      {fields[7]}     {fields[8]}        {fields[9]}")

    note = ("Commercial-style root-kink planform; synthetic chord and airfoil assignment on the "
            "NREL 5MW station radii, prebend, sweep and twist. Polars and structure inherited, not re-derived.")
    out = list(lines)
    out[1] = note
    out[first_row:first_row + len(PLANFORM)] = rebuilt
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out) + "\n", encoding="latin-1", newline="\n")

    print(f"Wrote {target}")
    print(f"{'r (m)':>7}  {'chord':>6}  {'afid':>4}   {'d_chord/dr':>10}")
    previous = None
    for span, (chord, afid) in zip(spans, PLANFORM):
        r = span + HUB_RAD
        slope = "" if previous is None else f"{(chord - previous[1]) / (r - previous[0]):+10.3f}"
        print(f"{r:7.2f}  {chord:6.3f}  {afid:4d}   {slope:>10}")
        previous = (r, chord)
    peak_index = max(range(len(PLANFORM)), key=lambda i: PLANFORM[i][0])
    peak_r = spans[peak_index] + HUB_RAD
    print(f"\nmax chord {PLANFORM[peak_index][0]:.3f} m at r={peak_r:.2f} m "
          f"(r/R={peak_r / 63.0:.3f}); max/root = {PLANFORM[peak_index][0] / PLANFORM[0][0]:.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--target", type=Path, default=TARGET)
    args = parser.parse_args()
    build(args.source, args.target)

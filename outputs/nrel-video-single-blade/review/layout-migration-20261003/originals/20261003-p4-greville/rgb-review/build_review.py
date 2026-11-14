"""RGB-only, sparse visual review locations; never writes fit observations.

The frozen locations below were selected after viewing the complete native image
and unaltered RGB crops. Pixel-column intensity drops were used only to locate
the visible transition to within a few pixels. No model, projection, 3-D truth,
or saved contour coordinates are loaded.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
P3 = ROOT / "outputs/nrel-video-single-blade/20261003-p3-center-scale/rgb"
OBS = ROOT / "outputs/nrel-video-single-blade/20261002-second/observations"
NATIVE = P3 / "C1_000042_native_rgb.png"
VALID = OBS / "C1_000042_contour_valid.png"
HISTORICAL = [VALID, OBS / "C1_000042_FBU.png", OBS / "C1_000042_contour.npy"]

# Approximate visual-review locations, with a deliberately conservative +/-3 px
# review tolerance. This is neither a validated measurement uncertainty nor a
# new observation file. The entries are NOT a dense or interpolated outline.
SEGMENTS = [
    dict(id="R1", kind="target_outer_boundary", decision="retain_future_candidate",
         anchors=[[1050, 912], [1100, 913], [1150, 916], [1200, 920]],
         region=[1050, 907, 1201, 925],
         reason="目标白色表面下缘与深色背景连续分开；背景纹理止于边界，未见杆件切过。"),
    dict(id="R2", kind="target_outer_boundary", decision="retain_future_candidate",
         anchors=[[1200, 920], [1250, 922], [1300, 923], [1350, 924], [1400, 925]],
         region=[1200, 915, 1401, 930],
         reason="下缘缓慢向下弯，白色叶片上侧和深色背景下侧可连续追踪；不是叶片内部阴影边。"),
    dict(id="R3", kind="target_outer_boundary", decision="retain_future_candidate",
         anchors=[[1400, 925], [1450, 925], [1500, 924], [1550, 922], [1600, 921], [1650, 919], [1700, 914]],
         region=[1400, 909, 1701, 930],
         reason="下缘继续连续，内部灰色弯曲阴影位于白色表面内，并未替代或截断外边界。"),
    dict(id="R4", kind="target_outer_boundary", decision="retain_future_candidate",
         anchors=[[1700, 914], [1725, 910], [1750, 904], [1775, 896]],
         region=[1700, 891, 1776, 919],
         reason="右端外边界上弯，仍为白色叶片对深色背景；此段所有锚点及±3 px邻域均被旧域排除。"),
    dict(id="J1", kind="target_outer_boundary_domain_join", decision="defer_domain_join",
         anchors=[[1775, 896], [1780, 895], [1790, 891], [1800, 888], [1810, 884]],
         region=[1775, 879, 1811, 901],
         reason="RGB归属清楚，但跨越既有valid域下边界；未来局部域需处理容差与旧观测去重，本轮不作为整段新增候选。"),
    dict(id="E1", kind="external_occluder_edges", decision="exclude",
         anchors=[], region=[75, 510, 246, 1075],
         reason="左侧灰色与黑色竖向杆件覆盖白色叶片；其侧缘和圆钝末端属于遮挡物，不是B1自由外轮廓。"),
    dict(id="U1", kind="occluded_target_boundary", decision="uncertain_do_not_infer",
         anchors=[], region=[120, 995, 195, 1055],
         reason="下缘接近杆件末端时出现相交与遮挡；RGB无法直接给出杆件后目标边界位置，禁止连接或外推补点。"),
    dict(id="E2", kind="detached_external_structure", decision="exclude",
         anchors=[[365, 61], [650, 119], [875, 166], [897, 132]],
         region=[360, 48, 907, 179],
         reason="上方白色细杆与目标白色叶片之间有可见深色背景间隔，不能因同为白色而并入B1轮廓。"),
    dict(id="E3", kind="internal_surface_shading", decision="exclude",
         anchors=[], region=[1430, 480, 1560, 880],
         reason="白色叶片内部的灰色弯曲过渡两侧仍是目标表面，非前景/背景分界，不应作为外轮廓。"),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def font(size: int):
    candidate = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
    return ImageFont.truetype(str(candidate), size) if candidate.exists() else ImageFont.load_default()


def main() -> None:
    baseline_path = OUT / "input_hashes_before.json"
    inputs = [NATIVE, *HISTORICAL, P3 / "README.md", P3 / "root_rgb_audit.json"]
    before = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    if baseline_path.exists():
        assert json.loads(baseline_path.read_text()) == before, "Reviewed source changed"
    else:
        baseline_path.write_text(json.dumps(before, indent=2) + "\n")

    im = Image.open(NATIVE).convert("RGB")
    assert im.size == (1920, 1080)
    valid = np.asarray(Image.open(VALID)) != 0
    provenance = json.loads((P3 / "root_rgb_audit.json").read_text())
    meta = next(x for x in provenance["rgb_frames"] if x["camera"] == "C1" and x["frame"] == 42)
    assert sha(NATIVE) == meta["png_sha256"]
    rgb_sha = hashlib.sha256(np.asarray(im).tobytes()).hexdigest()
    assert rgb_sha == meta["rgb_sha256"]
    for p in HISTORICAL:
        assert sha(p) == provenance["input_sha256"][str(p)]

    crops = {
        "lower_context": (0, 760, 1920, 1080),
        "root_lower": (950, 850, 1850, 1020),
        "left_structure": (0, 450, 600, 1080),
        "upper_context": (220, 0, 1150, 350),
    }
    for name, box in crops.items():
        im.crop(box).save(OUT / (name + "_rgb.png"))

    rows = []
    for segment in SEGMENTS:
        for x, y in segment["anchors"]:
            rows.append(dict(segment=segment["id"], x=x, y=y,
                             approximate_review_tolerance_px=3,
                             existing_contour_valid=bool(valid[y, x]),
                             any_valid_in_3px_square=bool(valid[max(0, y-3):y+4, max(0, x-3):x+4].any()),
                             decision=segment["decision"]))
    retained = [r for r in rows if r["decision"] == "retain_future_candidate"]
    assert retained and all(not r["any_valid_in_3px_square"] for r in retained)
    with (OUT / "review_locations.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    palette = {"retain_future_candidate": "#36e895", "defer_domain_join": "#f7c05a",
               "exclude": "#ff6969", "uncertain_do_not_infer": "#f7c05a"}
    box = crops["root_lower"]
    crop = im.crop(box)
    w, h = crop.size
    board = Image.new("RGB", (w, 2*h + 126), "#152029")
    d = ImageDraw.Draw(board)
    board.paste(crop, (0, 28))
    board.paste(crop, (0, h+65))
    d.text((10, 5), "Native RGB crop: x=950:1850, y=850:1020 (no resampling)", font=font(16), fill="white")
    d.text((10, h+40), "Sparse review locations only; no model projection; no new fit observations", font=font(16), fill="white")
    for segment in SEGMENTS[:5]:
        pts = [(x-box[0], y-box[1]+h+65) for x, y in segment["anchors"]]
        color = palette[segment["decision"]]
        # The displayed segments connect review anchors for legibility only.
        d.line(pts, fill=color, width=2)
        for x, y in pts:
            d.ellipse((x-2, y-2, x+2, y+2), fill=color)
        x, y = pts[len(pts)//2]
        d.text((x-10, y+15 if segment["id"] != "J1" else y-35), segment["id"], font=font(17), fill=color)
    for native_y, label, label_x in [(896, "saved exclusion y>=896", 8), (888, "valid ends at row 887", 360)]:
        yy = native_y-box[1]+h+65
        for xx in range(0, w, 14):
            d.line((xx, yy, min(xx+6, w-1), yy), fill="#6bc8fa", width=1)
        d.text((label_x, yy-17), label, font=font(13), fill="#6bc8fa")
    d.text((10, 2*h+76), "Green: future candidate. Amber J1: join/de-duplication pending. Review tolerance: +/-3 px (not calibrated).", font=font(14), fill="white")
    d.text((10, 2*h+99), "All coordinates use native top-left origin; connecting lines are only a display aid.", font=font(14), fill="white")
    board.save(OUT / "root_lower_review.png")

    context = Image.new("RGB", (1920, 1160), "#152029")
    context.paste(im, (0, 0))
    d = ImageDraw.Draw(context)
    for segment in SEGMENTS:
        color = palette[segment["decision"]]
        x1, y1, x2, y2 = segment["region"]
        d.rectangle((x1, y1, x2, y2), outline=color, width=2)
        d.text((x1+3, max(0, y1-25)), segment["id"], font=font(20), fill=color, stroke_width=2, stroke_fill="#152029")
    d.text((20, 1092), "C1 #42 | RGB-only review | R1-R4 future candidates; J1 domain join; E1/E2 structures; E3 surface shading; U1 hidden boundary", font=font(22), fill="white")
    d.text((20, 1128), "Boxes locate reviewed regions only. No geometry projected, no observation or mask changed, no fit performed.", font=font(22), fill="white")
    context.save(OUT / "native_context_review.png")

    after = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    assert before == after
    unique_retained = len({(r["x"], r["y"]) for r in retained})
    report = {
        "schema": "nrel.rgb_boundary_visual_review.v1",
        "status": "REVIEW_ONLY_NO_OBSERVATION_CHANGE",
        "date": "2026-10-03", "camera": "C1", "frame": 42, "frame_role": "fit",
        "native_size": [1920, 1080], "coordinate_convention": "top-left origin; x right; y down; integer pixel centers",
        "scope": "P3-identified lower excluded RGB region and contextual false boundaries; no 3-D root-band membership inferred from RGB",
        "selection_basis": "complete native RGB and unaltered RGB crops, target surface/background continuity and visible foreign structures",
        "location_method": "visual selection followed by sparse vertical RGB intensity-drop checks; rounded approximate anchors; no dense edge extraction",
        "not_blind_to_previous_diagnostics": True,
        "prohibited_for_selection": ["distance to fitted mesh", "frozen mesh projection", "truth geometry", "optimization loss"],
        "model_or_truth_loaded_for_this_review": False,
        "fitting": False, "observations_modified": False, "masks_modified": False,
        "coordinate_only_p4_fit_inputs_unchanged": True,
        "approximate_review_tolerance_px": 3,
        "tolerance_is_calibrated": False,
        "review_locations_are_fit_observations": False,
        "curve_interpolation_is_observation": False,
        "segments": SEGMENTS,
        "retained_segment_count": 4,
        "retained_unique_sparse_location_count": unique_retained,
        "retained_locations_and_3px_neighborhoods_all_currently_invalid": True,
        "rgb_sha256": rgb_sha,
        "matches_p3_pixel_sha256": True,
        "matches_p3_historical_observation_file_sha256": True,
        "input_sha256_before": before, "input_sha256_after": after, "inputs_unchanged": before == after,
        "crops_native_xyxy_half_open": crops,
        "future_branch_recommendation": {
            "may_enter_separate_observation_version_review": ["R1", "R2", "R3", "R4"],
            "pending_join_decision": ["J1"],
            "never_infer_hidden_target_from_occluder": ["E1", "U1"],
            "exclude_false_outline": ["E2", "E3"],
            "next_step": "independent dense RGB annotation/local valid-domain version with uncertainty, provenance and deduplication; freeze it before model response or fit; original coordinate-only P4 comparison uses unchanged observations",
            "does_not_establish": ["new observation count or independent information", "3-D root-band span", "root error reduction", "complete surface recovery", "geometric acceptance"],
        },
    }
    (OUT / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "retained_unique_locations": unique_retained,
                      "inputs_unchanged": report["inputs_unchanged"], "rgb_sha256": rgb_sha}))


if __name__ == "__main__":
    main()

"""Compare frozen reconstruction outputs against RGB observations, never truth.

Call only after models/configuration are frozen. Previously inspected frames 44
and 45 are explicitly nonblind temporal validation, not an independent test.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .artifact_paths import relocated_path
from .boundary_metrics import boundary_metrics
from .diagnostics import hard_silhouette, read_mesh_ply
from .renderer import scale_intrinsics


def _permitted_path(path: str | Path) -> Path:
    path = relocated_path(path)
    if any(part in {"evaluation-only", "internal-capture"} for part in path.parts):
        raise ValueError("RGB comparison must not read evaluation truth or internal capture")
    if "truth" in path.name.lower():
        raise ValueError("RGB comparison must not read a truth-labelled file")
    return path


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024),b""):
            hasher.update(block)
    return hasher.hexdigest()


def _array_file(path: str | Path) -> np.ndarray:
    path = _permitted_path(path)
    if path.suffix.lower() == ".npy":
        return np.load(path,allow_pickle=False)
    with Image.open(path) as image:
        return np.array(image,copy=True)


def _split_role(item: Mapping[str,Any]) -> tuple[str,str]:
    declared = str(item.get("split","fit"))
    frame = int(item["frame_id"])
    if declared == "fit":
        return "fit", "SEEN_TRAINING_FRAME" if frame in (44,45) else "FIT"
    return "nonblind_temporal_validation", "NONBLIND_TEMPORAL_VALIDATION"


def _matrix(value: Any,shape: tuple[int,int]) -> np.ndarray:
    array = np.asarray(value,dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"Expected finite {shape} calibration/pose")
    return array


def _prepare(item: Mapping[str,Any],image_size: tuple[int,int]) -> dict[str,Any]:
    label_path = _permitted_path(item["labels_path"])
    labels_source = _array_file(label_path)
    if labels_source.ndim != 2 or not np.isin(labels_source,[0,1,2]).all():
        raise ValueError("RGB comparison requires B=0/F=1/U=2 label images")
    source_size = (labels_source.shape[1],labels_source.shape[0])
    labels = np.array(Image.fromarray(labels_source.astype(np.uint8)).resize(image_size,Image.Resampling.NEAREST),copy=True)
    paths = [label_path]
    contour = valid = None
    if item.get("contour_points_path") is not None:
        if item.get("contour_valid_path") is None:
            raise ValueError("RGB contour points require contour_valid_path with separate occlusion/cropping exclusions")
        point_path = _permitted_path(item["contour_points_path"])
        if point_path.suffix.lower() != ".npy":
            raise ValueError("RGB contour_points_path must be a safe numeric .npy array")
        contour_source = np.load(point_path,allow_pickle=False)
        if contour_source.ndim != 2 or contour_source.shape[1] != 2 or not np.isfinite(contour_source).all():
            raise ValueError("RGB contours must be finite Nx2 x/y pixel-centre points")
        valid_path = _permitted_path(item["contour_valid_path"])
        valid_source = _array_file(valid_path)
        if valid_source.shape != labels_source.shape or not np.isfinite(valid_source).all() or not np.isin(valid_source,[0,1,255,False,True]).all():
            raise ValueError("Separate contour valid mask must be binary and match source labels")
        valid = np.array(Image.fromarray((valid_source!=0).astype(np.uint8)).resize(image_size,Image.Resampling.NEAREST),copy=True).astype(bool)
        scale = np.array(image_size,dtype=float)/np.array(source_size,dtype=float)
        contour = (contour_source.astype(float)+.5)*scale-.5
        paths.extend((point_path,valid_path))
    K = scale_intrinsics(_matrix(item["K"],(3,3)),source_size,image_size)
    camera = _matrix(item["T_camera_cv_from_world"],(4,4))
    root = _matrix(item["T_world_from_blade_root"],(4,4))
    for transform in (camera,root):
        if not np.allclose(transform[3],[0,0,0,1],atol=1e-6) or not np.allclose(transform[:3,:3].T@transform[:3,:3],np.eye(3),atol=1e-3) or np.linalg.det(transform[:3,:3])<.999:
            raise ValueError("RGB comparison needs proper rigid camera/root poses in metres")
    if K[0,0]<=0 or K[1,1]<=0 or not np.allclose(K[2],[0,0,1],atol=1e-8):
        raise ValueError("RGB comparison needs a pinhole K with positive focal lengths")
    return {"labels":labels,"K":K,"camera":camera,"root":root,"contour":contour,"valid":valid,
            "source_size":source_size,"paths":paths}


def _frame_statistics(prediction: np.ndarray,labels: np.ndarray,boundary: Mapping[str,Any]) -> dict[str,Any]:
    foreground,background,certain = labels==1,labels==0,labels!=2
    union = (prediction|foreground)&certain
    missing = []
    if not foreground.any(): missing.append("NO_FOREGROUND_PIXELS")
    if not background.any(): missing.append("NO_BACKGROUND_PIXELS")
    if not union.any(): missing.append("NO_CERTAIN_UNION_PIXELS")
    for direction in ("observed_to_candidate","candidate_to_observed"):
        if boundary[direction]["null_reason"]:
            missing.append(direction+":"+boundary[direction]["null_reason"])
    return {"foreground_coverage_fraction":float(prediction[foreground].mean()) if foreground.any() else None,
            "background_spill_fraction":float(prediction[background].mean()) if background.any() else None,
            "masked_iou":float((prediction&foreground).sum()/union.sum()) if union.any() else None,
            "foreground_pixels":int(foreground.sum()),"background_pixels":int(background.sum()),
            "uncertain_pixels":int((labels==2).sum()),"projected_pixels_in_image":int(prediction.sum()),
            "boundary":dict(boundary),"missing_terms":missing}


def _aggregate(rows: Sequence[Mapping[str,Any]]) -> dict[str,Any]:
    def values(row: Mapping[str,Any]) -> dict[str,Any]:
        return {**{key:row[key] for key in ("foreground_coverage_fraction","background_spill_fraction","masked_iou")},
                **{f"boundary_{direction}_{metric}":row["boundary"][direction][metric]
                   for direction in ("observed_to_candidate","candidate_to_observed") for metric in ("mean_px","p95_px")}}
    metric_names = list(values(rows[0])) if rows else []
    metrics = {}
    for name in metric_names:
        measured = [values(row)[name] for row in rows if values(row)[name] is not None]
        metrics[name] = {"mean":float(np.mean(measured)) if measured else None,
                         "min":float(np.min(measured)) if measured else None,
                         "max":float(np.max(measured)) if measured else None,
                         "valid_frame_count":len(measured),"missing_frame_count":len(rows)-len(measured)}
    return {"frame_count":len(rows),"frame_ids":[int(row["frame_id"]) for row in rows],"metrics":metrics,
            "missing_terms_by_frame":[{"camera_id":row["camera_id"],"frame_id":row["frame_id"],"missing_terms":row["missing_terms"]} for row in rows if row["missing_terms"]]}


def compare_rgb(model_paths: Mapping[str,str|Path], observations: Sequence[Mapping[str,Any]]|str|Path,
                output_dir: str|Path, *, image_size: tuple[int,int]=(960,540),
                reporting_size: tuple[int,int]=(1920,1080)) -> dict[str,Any]:
    """Compare frozen initial/v1/smooth/v2 PLYs using permitted RGB inputs only.

    Observations use ``contour_points_path`` and ``contour_valid_path`` in native
    label-image pixel coordinates.  Without those fields, the conservative F--B
    boundary definition is retained and may be null. No optimization is run.
    """
    image_size,reporting_size = tuple(map(int,image_size)),tuple(map(int,reporting_size))
    if len(image_size)!=2 or len(reporting_size)!=2 or min(*image_size,*reporting_size)<=0:
        raise ValueError("Positive (width,height) render/report sizes required")
    if not model_paths:
        raise ValueError("No frozen reconstruction models to compare")
    observation_path = None
    if isinstance(observations,(str,Path)):
        observation_path = _permitted_path(observations)
        observations = json.loads(observation_path.read_text(encoding="utf-8"))
    observations = list(observations)
    if not observations:
        raise ValueError("No RGB observations")
    keys = [(str(item["camera_id"]),int(item["frame_id"])) for item in observations]
    if len(keys)!=len(set(keys)):
        raise ValueError("Duplicate camera/frame observations")
    meshes,paths = {},{}
    for name,path in model_paths.items():
        path = _permitted_path(path)
        vertices,faces = read_mesh_ply(path)
        vertices,faces = np.asarray(vertices,dtype=float).reshape((-1,3)),np.asarray(faces,dtype=int).reshape((-1,3))
        if not np.isfinite(vertices).all() or (faces.size and (faces.min()<0 or faces.max()>=len(vertices))):
            raise ValueError(f"Invalid frozen model: {name}")
        meshes[str(name)] = (vertices,faces);paths[str(name)] = path
    report: dict[str,Any] = {"schema":"nrel-frozen-rgb-comparison.v2","status":"RGB_COMPARISON_COMPLETE_GEOMETRY_UNSCORED",
        "render_size_wh":list(image_size),"reporting_size_wh":list(reporting_size),"boundary_units":"reporting-frame px",
        "projection_contract":"hard CPU triangle union at pixel centres; half-pixel K and contour resize",
        "aggregate_method":"macro per-frame means; per-camera metrics remain separate; no conversion to 3D error",
        "input_policy":{"evaluation_truth_read":False,"source_replay_read":False,"optimization_run":False},
        "validation_policy":{"frame_44_45":"Previously inspected: NONBLIND_TEMPORAL_VALIDATION if excluded from fitting; SEEN_TRAINING_FRAME if declared fit.","blind_claim":False},
        "model_signatures":{name:{"path":str(path),"sha256":_digest(path)} for name,path in paths.items()},
        "observation_file_signature":_digest(observation_path) if observation_path is not None else None,
        "observation_asset_signatures":{},"per_frame":[],"split_aggregates":{}}
    scale = (reporting_size[0]/image_size[0],reporting_size[1]/image_size[1])
    for item in observations:
        prepared = _prepare(item,image_size)
        split,status = _split_role(item)
        for path in prepared["paths"]:
            report["observation_asset_signatures"][str(path)] = _digest(path)
        for name,(vertices,faces) in meshes.items():
            prediction = hard_silhouette(vertices,faces,prepared["K"],prepared["camera"],prepared["root"],image_size) if len(faces) else np.zeros((image_size[1],image_size[0]),dtype=bool)
            boundary = boundary_metrics(prepared["labels"],prediction,observed_boundary_points=prepared["contour"],candidate_valid_mask=prepared["valid"],pixel_size_px=scale)
            row = {"model":name,"camera_id":str(item["camera_id"]),"frame_id":int(item["frame_id"]),
                   "sim_time_s":item.get("sim_time_s"),"declared_split":item.get("split","fit"),"split":split,
                   "validation_status":status,"labels_source_size_wh":list(prepared["source_size"]),
                   **_frame_statistics(prediction,prepared["labels"],boundary)}
            if int(item["frame_id"]) in (44,45) and item.get("split","fit")=="fit":
                row["missing_terms"].append("FRAME44_45_USED_IN_FIT_NOT_VALIDATION")
            report["per_frame"].append(row)
    for name in meshes:
        report["split_aggregates"][name] = {}
        for split in sorted({row["split"] for row in report["per_frame"] if row["model"]==name}):
            selected = [row for row in report["per_frame"] if row["model"]==name and row["split"]==split]
            report["split_aggregates"][name][split] = {"all_cameras":_aggregate(selected),"per_camera":{
                camera:_aggregate([row for row in selected if row["camera_id"]==camera]) for camera in sorted({row["camera_id"] for row in selected})}}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    report["output_json"] = str((output_dir/"rgb_comparison.json").resolve())
    (output_dir/"rgb_comparison.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    return report


def main(argv: Sequence[str]|None=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-json",type=Path,required=True,help="Frozen name->PLY path mapping")
    parser.add_argument("--observations",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--width",type=int,default=960);parser.add_argument("--height",type=int,default=540)
    args = parser.parse_args(argv)
    models = json.loads(_permitted_path(args.models_json).read_text(encoding="utf-8"))
    result = compare_rgb(models,args.observations,args.output_dir,image_size=(args.width,args.height))
    print(json.dumps({"status":result["status"],"output":result["output_json"],"comparisons":len(result["per_frame"])},ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

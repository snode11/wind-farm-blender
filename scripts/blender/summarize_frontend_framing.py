"""Make a local contact table and CSV from compare_frontend_framing evidence."""
from __future__ import annotations
import csv
import html
import json
from pathlib import Path
import sys


def build(directory):
    out = Path(directory).resolve()
    manifest = json.loads((out / "manifest.json").read_text())
    candidates = json.loads((out / "candidates.json").read_text())
    candidate_map = {c["id"]: c for c in candidates}
    records = [json.loads(path.read_text()) for path in sorted((out / "metrics").glob("*.json"))]
    rows = []
    for record in records:
        for sample in record["samples"]:
            v = sample["visibility"]
            for c in sample["cameras"]:
                rows.append({"candidate": record["id"], "stage": sample["stage"], "camera": c["camera"],
                             "blade": "Blade1", "frame": sample["frame"], "time_s": sample["time_s"],
                             "turn_deg": sample["turn_deg"], "visible_length_m": v["visible_length_m"],
                             "longest_contiguous_m": v["longest_contiguous_sampled_length_m"],
                             "gap_intervals_m": json.dumps(v["gap_intervals_m"]),
                             "overlap_C1_C2_m": v["adjacent_common_surface"][0]["length_m"],
                             "overlap_C2_C3_m": v["adjacent_common_surface"][1]["length_m"],
                             "target_pixels": c["target_pixels"], "frame_pixels": c["frame_pixels"],
                             "target_pixel_fraction": c["target_pixel_fraction"],
                             "root_tip_counts": json.dumps(c["root_tip"]),
                             "blockers": json.dumps(c["blockers"]), "image": c["image"]})
    if rows:
        with (out / "metrics.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    content = ["<!doctype html><html lang='zh'><meta charset='utf-8'><title>三相机候选取景联系表</title>",
        "<style>body{font:15px system-ui,sans-serif;background:#141a22;color:#e3e9ef;margin:24px}a{color:#83c6ff}h1{font-size:26px}h2{margin-top:40px}table{border-collapse:collapse;width:100%;max-width:1250px}th,td{border:1px solid #425367;padding:8px;text-align:left}img{width:100%;display:block;aspect-ratio:16/9}figure{margin:0}figcaption{font-size:12px;padding:6px 0}.views{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;max-width:1250px}.stamp{max-width:1226px;padding:12px;background:#253142;margin-top:20px}code{white-space:pre-wrap}summary{cursor:pointer;padding:12px;background:#202c3b}</style>",
        "<h1>15 组三相机候选：同刻技术帧联系表</h1>",
        "<p>绿色 = 同一目标 Blade1；灰色 = 其他实体。真实盒体、支架、机舱和叶片保留。每帧 320×180，全部候选使用相同内参与技术渲染设置。图像用于取景与遮挡比较，不代表材质质量、原图采集质量、播放流畅度或硬件验收。</p>",
        f"<p>状态：{html.escape(manifest['status'])} · Blender {html.escape(manifest['blender'])} · <a href='manifest.json'>环境与口径</a> · <a href='candidates.json'>15 组参数/约束</a> · <a href='metrics.csv'>完整 CSV</a> · <a href='baseline-layout.json'>恢复基线 JSON</a></p>",
        "<p>区间表示每个纵向采样格至少有可见表面样本，不证明整个叶片表面可见。共同区域只计两路均看见的同一表面三角形样本。固定相机允许目标转出视野。</p>",
        "<table><tr><th>候选</th><th>安装几何</th><th>相对转向</th><th>粗筛均值：最长连续/并集/较小共同长度/像素占比</th><th>加密</th></tr>"]
    ranks = {r["id"]: r for r in manifest.get("coarse_rank", [])}
    for c in candidates:
        rank = ranks.get(c["id"])
        values = " / ".join(f"{value:.3f}" for value in rank["rank"]) if rank else "未比较"
        content.append(f"<tr><td><a href='#{c['id']}'>{c['id']}</a></td><td>{c['installation_status']}</td><td>{c['actual_relative_aim_deg']}°</td><td>{values}</td><td>{'是' if c['id'] in manifest.get('finalists', []) else '否'}</td></tr>")
    content.append("</table>")
    for record in records:
        c = candidate_map[record["id"]]
        stage = "周期加密" if "transition_extra_angles_deg" in record else "多相位粗筛"
        mount = c["layout"]["rig_pose"]["surface_mount"]
        content.append(f"<h2 id='{c['id']}'>{html.escape(c['id'])} · {stage}</h2><p>安装点 {html.escape(str(mount['point']))} m；盒体实际朝向 {mount['aim_deg']:.1f}°；相对当前 {c['actual_relative_aim_deg']:.1f}° · <a href='layouts/{c['id']}.json'>复现 JSON</a></p>")
        content.append("<details open><summary>同刻 C1 → C2 → C3；所有图片完整视場</summary>")
        for sample in record["samples"]:
            v = sample["visibility"]
            overlaps = [o["length_m"] for o in v["adjacent_common_surface"]]
            content.append(f"<div class='stamp'>Blade1 · t={sample['time_s']:.4f} s · frame {sample['frame']} · 相位 {sample['turn_deg']:.2f}° · 可见并集 {v['visible_length_m']:.2f} m · 最长连续 {v['longest_contiguous_sampled_length_m']:.2f} m · C1–C2/C2–C3 同表面共同长度 {overlaps[0]:.2f}/{overlaps[1]:.2f} m<br>缺口：{html.escape(str(v['gap_intervals_m']))}</div><div class='views'>")
            for camera in sample["cameras"]:
                content.append(f"<figure><a href='{camera['image']}'><img loading='lazy' src='{camera['image']}' alt='{c['id']} {camera['camera']} Blade1 t={sample['time_s']:.4f}'></a><figcaption>{camera['camera']} · Blade1 · {camera['target_pixels']}/{camera['frame_pixels']} px ({100*camera['target_pixel_fraction']:.2f}%) · 射线可见 {camera['visible_surface_samples']} 个表面样本</figcaption></figure>")
            content.append("</div>")
        content.append("</details>")
    content.append("</html>")
    (out / "contact-table.html").write_text("\n".join(content), encoding="utf-8")
    print(json.dumps({"contact_table": str(out / "contact-table.html"), "csv_rows": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    build(sys.argv[1])

"""Self-contained previews of reconstruction output; never reads evaluation truth."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

import numpy as np

from .evaluate import Mesh, read_ply


_HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NREL T1/B1 单叶片模型</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101823;color:#e8eef7;font:15px/1.55 system-ui,-apple-system,sans-serif}
main{max-width:1260px;margin:auto;padding:24px}h1{font-size:24px;font-weight:650;margin:0 0 5px}.meta{color:#a8b8cc;margin-bottom:18px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:18px}.viewer{background:#152233;border:1px solid #2b4059;border-radius:14px;overflow:hidden}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:12px 15px;border-bottom:1px solid #2b4059}
select,button{background:#21344b;border:1px solid #476180;color:#eef5ff;border-radius:7px;padding:7px 10px;font:inherit}button{cursor:pointer}
label{font-size:13px}canvas{display:block;width:100%;height:620px;touch-action:none;cursor:grab}canvas:active{cursor:grabbing}
.hint{font-size:12px;color:#a8b8cc;padding:9px 15px;border-top:1px solid #2b4059}.card{background:#152233;border:1px solid #2b4059;border-radius:12px;padding:15px;margin-bottom:14px}
.card strong{display:block;margin-bottom:7px}.card p{margin:7px 0;color:#bccada;font-size:13px}.warning{border-color:#896538;color:#ffe0ad}.legend{display:flex;gap:9px;align-items:center;margin:9px 0;font-size:13px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}.stat{color:#96b7dc!important}.fine{color:#8397b0;font-size:12px;margin-top:16px}
@media(max-width:850px){main{padding:14px}.layout{grid-template-columns:1fr}aside{display:grid;grid-template-columns:1fr 1fr;gap:12px}.card{margin:0}canvas{height:530px}}
@media(max-width:520px){aside{display:block}.card{margin-bottom:12px}h1{font-size:21px}canvas{height:470px}}
</style></head><body><main>
<h1>NREL 5MW · T1/B1 单叶片模型</h1><div class="meta" id="meta"></div>
<div class="layout"><section class="viewer"><div class="controls">
<select id="mode" aria-label="选择模型"><option value="final">视频优化结果</option><option value="initial">初始模板</option><option value="prior_only">无图像对照</option><option value="overlay">初始与视频结果叠加</option></select>
<label><input id="wire" type="checkbox"> 网格线</label><button id="reset">重置视角</button>
</div><canvas id="view" aria-label="可旋转缩放的单叶片三维模型"></canvas>
<div class="hint">拖动旋转 · 滚轮缩放 · Shift＋拖动平移 · 双击重置。正交显示，保留物理比例。</div></section>
<aside><div class="card"><strong>模型与坐标</strong><p id="identity"></p><p id="stats" class="stat"></p>
<div class="legend"><span class="dot" style="background:#57c9ec"></span>视频优化结果</div>
<div class="legend"><span class="dot" style="background:#f2ba71"></span>初始独立模板</div>
<div class="legend"><span class="dot" style="background:#b5a3ed"></span>无图像正则化对照</div>
<p>x：轴向／厚度；y：弦向；z：展向。所有坐标和尺寸以米计。</p></div>
<div class="card warning"><strong>恢复范围说明</strong><p>完整闭合的网格不等于完整恢复。未观测的表面可由模板闭合。</p>
<p>背面、厚度与扭转仍依赖先验；本预览不把闭合表面标为已被视频完整观察。</p><p>几何贡献结论见独立评分；此页面只显示重建输出。</p></div></aside></div>
<div class="fine">本文件内嵌全部网格与显示代码，无远端脚本或 CDN。数据仅来自 reconstruction 的初始、无图像及视频结果 PLY，未读取评分真值。</div>
</main><script>
'use strict';
const payload = __PAYLOAD__;
const models = payload.models;
const canvas = document.getElementById('view'), context = canvas.getContext('2d');
const mode = document.getElementById('mode'), wire = document.getElementById('wire');
const names = {initial:'初始模板', prior_only:'无图像对照', final:'视频优化结果'};
const colors = {initial:[242,186,113],prior_only:[181,163,237],final:[87,201,236]};
const allPoints = Object.values(models).flatMap(model=>model.vertices);
const bounds = [0,1,2].map(axis=>[Math.min(...allPoints.map(v=>v[axis])),Math.max(...allPoints.map(v=>v[axis]))]);
const centre = bounds.map(pair=>(pair[0]+pair[1])/2);
const extent = Math.max(...bounds.map(pair=>pair[1]-pair[0]),1);
let yaw=-0.35,pitch=0.10,zoom=1,panX=0,panY=0,drag=null,width=0,height=0,dirty=true;
const identity=payload.metadata;
document.getElementById('meta').textContent=`叶根局部坐标 · 单位 m · 参考时刻 t_ref = ${Number(identity.t_ref_sim_time_s).toFixed(6)} s`;
document.getElementById('identity').textContent=`${identity.target}，${identity.coordinate_frame}。同一参考状态下比较三份输出。`;
function rotate(v){const x=v[0]-centre[0],y=v[1]-centre[1],z=v[2]-centre[2];
const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
const horizontal=y*cy-x*sy,depth=x*cy+y*sy;
return [horizontal,z*cp-depth*sp,depth*cp+z*sp];}
function project(v){const p=rotate(v),scale=Math.min(width,height)*.79/extent*zoom;
return [width/2+panX+p[0]*scale,height/2+panY-p[1]*scale,p[2]];}
function faceNormal(a,b,c){const u=b.map((v,i)=>v-a[i]),v=c.map((v,i)=>v-a[i]);
return [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]];}
function reset(){yaw=-0.35;pitch=.10;zoom=1;panX=0;panY=0;dirty=true;}
function draw(){const ratio=Math.min(window.devicePixelRatio||1,2),rect=canvas.getBoundingClientRect();
width=rect.width;height=rect.height;if(canvas.width!==Math.round(width*ratio)||canvas.height!==Math.round(height*ratio)){
canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);}
context.setTransform(ratio,0,0,ratio,0,0);context.clearRect(0,0,width,height);
const gradient=context.createLinearGradient(0,0,0,height);gradient.addColorStop(0,'#192d42');gradient.addColorStop(1,'#111d2c');
context.fillStyle=gradient;context.fillRect(0,0,width,height);
context.strokeStyle='rgba(135,166,202,.075)';context.lineWidth=1;
for(let x=width/2%50;x<width;x+=50){context.beginPath();context.moveTo(x,0);context.lineTo(x,height);context.stroke();}
for(let y=height/2%50;y<height;y+=50){context.beginPath();context.moveTo(0,y);context.lineTo(width,y);context.stroke();}
let keys=mode.value==='overlay'?['initial','final']:[mode.value],triangles=[];
for(const key of keys){const model=models[key],vertices=model.vertices.map(project);
for(const face of model.faces){const p=face.map(i=>vertices[i]);
if(p.every(v=>v[0]<-2)||p.every(v=>v[0]>width+2)||p.every(v=>v[1]<-2)||p.every(v=>v[1]>height+2))continue;
const normal=faceNormal(...face.map(i=>model.vertices[i])),length=Math.hypot(...normal);
const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
const light=length?Math.abs((normal[0]*cy*cp+normal[1]*sy*cp+normal[2]*sp)/length):.4;
triangles.push({p,key,depth:p.reduce((sum,v)=>sum+v[2],0)/3,shade:.46+.52*light});}}
triangles.sort((a,b)=>a.depth-b.depth);
for(const triangle of triangles){const p=triangle.p,rgb=colors[triangle.key].map(v=>Math.round(v*triangle.shade));
context.beginPath();context.moveTo(p[0][0],p[0][1]);context.lineTo(p[1][0],p[1][1]);context.lineTo(p[2][0],p[2][1]);context.closePath();
const overlayInitial=mode.value==='overlay'&&triangle.key==='initial';
context.fillStyle=`rgba(${rgb.join(',')},${overlayInitial?.16:.96})`;context.fill();
if(wire.checked||overlayInitial){context.strokeStyle=overlayInitial?'rgba(242,186,113,.5)':'rgba(4,15,29,.35)';context.lineWidth=.5;context.stroke();}}
const root=[0,0,0],base=project(root),axisLength=Math.min(6,extent*.12);
const axes=[{v:[axisLength,0,0],c:'#f18d86',n:'x'},{v:[0,axisLength,0],c:'#8cdfad',n:'y'},{v:[0,0,axisLength],c:'#a6c7ff',n:'z'}];
context.font='12px system-ui';context.lineWidth=1.5;
for(const axis of axes){const end=project(axis.v);context.strokeStyle=axis.c;context.fillStyle=axis.c;
context.beginPath();context.moveTo(base[0],base[1]);context.lineTo(end[0],end[1]);context.stroke();context.fillText(axis.n,end[0]+5,end[1]-4);}
const selected=mode.value==='overlay'?models.final:models[mode.value];
document.getElementById('stats').textContent=`${mode.value==='overlay'?'初始＋视频结果':names[mode.value]}：${selected.vertices.length} 顶点 / ${selected.faces.length} 三角面。展示全展向范围 ${bounds[2][0].toFixed(2)}–${bounds[2][1].toFixed(2)} m。`;
context.fillStyle='#9cb4d0';context.fillText(`缩放 ${zoom.toFixed(2)}×`,14,height-15);}
canvas.addEventListener('pointerdown',event=>{canvas.setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY};});
canvas.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
if(event.shiftKey){panX+=dx;panY+=dy;}else{yaw+=dx*.008;pitch=Math.max(-1.45,Math.min(1.45,pitch+dy*.008));}
drag={x:event.clientX,y:event.clientY};dirty=true;});
canvas.addEventListener('pointerup',()=>{drag=null;});canvas.addEventListener('pointercancel',()=>{drag=null;});
canvas.addEventListener('wheel',event=>{event.preventDefault();zoom=Math.max(.3,Math.min(30,zoom*Math.exp(-event.deltaY*.0015)));dirty=true;},{passive:false});
canvas.addEventListener('dblclick',reset);document.getElementById('reset').addEventListener('click',reset);
mode.addEventListener('change',()=>{dirty=true;});wire.addEventListener('change',()=>{dirty=true;});
new ResizeObserver(()=>{dirty=true;}).observe(canvas);
function tick(){if(dirty){dirty=false;draw();}requestAnimationFrame(tick);}tick();
</script></body></html>'''


def _safe_file(root: Path, name: str) -> Path:
    path = root / name
    if path.resolve().parent != root or not path.is_file():
        raise ValueError(f"Preview only accepts reconstruction-local output files: {name}")
    return path


def _static_preview(models: dict[str, Mesh], metadata: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    names = {"initial":"Initial independent template", "prior_only":"No-image control", "final":"Video-fitted result"}
    colours = {"initial":"#cb914f", "prior_only":"#a894d8", "final":"#3ab2d1"}
    fig = plt.figure(figsize=(12, 8), facecolor="#f6f8fb")
    all_vertices = np.concatenate([mesh.vertices for mesh in models.values()])
    lower, upper = all_vertices.min(axis=0), all_vertices.max(axis=0)
    extent = np.maximum(upper-lower, [.2,.2,.2])
    for index, key in enumerate(("initial","prior_only","final")):
        axis = fig.add_subplot(1,3,index+1,projection="3d")
        axis.add_collection3d(Poly3DCollection(models[key].triangles, facecolor=colours[key], edgecolor="none", alpha=.9))
        axis.set_xlim(lower[0]-.2,upper[0]+.2);axis.set_ylim(lower[1]-.2,upper[1]+.2);axis.set_zlim(lower[2],upper[2])
        axis.set_box_aspect(extent)
        axis.view_init(elev=5,azim=12)
        axis.set_title(names[key],fontsize=12)
        # A 61.5 m blade is very slender at physical aspect; crowded short-axis
        # ticks would obscure the root.  Preserve the long-axis metre scale.
        axis.set_xticks([]);axis.set_yticks([]);axis.set_xlabel("");axis.set_ylabel("")
        axis.set_zlabel("span z (m)",labelpad=9)
        axis.grid(False)
    fig.suptitle(f"NREL 5MW / T1 B1 · blade-root coordinates (m) · t_ref={float(metadata['t_ref_sim_time_s']):.6f} s",fontsize=14)
    fig.text(.5,.04,"Closed mesh does not establish full recovery. Backside, thickness and twist remain prior-dependent.\nReconstruction outputs only; no evaluation truth used in this preview.",ha="center",fontsize=11,color="#535f70")
    fig.subplots_adjust(left=.01,right=.99,bottom=.11,top=.91,wspace=0)
    fig.savefig(path,dpi=150)
    plt.close(fig)


def create_preview(reconstruction_dir: str | Path, *, static_png: bool = True) -> dict:
    """Read only the three declared reconstruction models and their metadata.

    Returns absolute paths to ``model_preview.html`` and optional PNG, saved in
    the same reconstruction directory.  The HTML needs no server or network.
    """
    root = Path(reconstruction_dir).resolve()
    state = json.loads(_safe_file(root,"model_state.json").read_text(encoding="utf-8"))
    metadata = state.get("reference_state",state)
    if metadata.get("target") != "T1/B1" or metadata.get("units") != "m" or metadata.get("coordinate_frame") != "blade_root_local":
        raise ValueError("Preview needs T1/B1 metre-scale blade_root_local model metadata")
    if not np.isfinite(float(metadata.get("t_ref_sim_time_s",float("nan")))):
        raise ValueError("Preview needs a finite declared t_ref")
    models = {name:read_ply(_safe_file(root,filename)) for name,filename in {
        "initial":"initial_template.ply","prior_only":"prior_only.ply","final":"T1_B1.ply"}.items()}
    if any(not len(mesh.vertices) or not len(mesh.faces) for mesh in models.values()):
        raise ValueError("Preview needs three nonempty triangle models")
    payload = {"metadata":{key:metadata[key] for key in ("target","units","coordinate_frame","t_ref_sim_time_s")},
               "models":{name:{"vertices":mesh.vertices.tolist(),"faces":mesh.faces.tolist()} for name,mesh in models.items()}}
    json_data = json.dumps(payload,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c")
    html_path = root/"model_preview.html"
    html_path.write_text(_HTML.replace("__PAYLOAD__",json_data),encoding="utf-8")
    result = {"status":"WRITTEN", "html":str(html_path), "source":"reconstruction models only", "truth_read":False}
    if static_png:
        png_path = root/"model_preview.png"
        _static_preview(models,metadata,png_path)
        result["png"] = str(png_path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reconstruction_dir",type=Path)
    parser.add_argument("--no-static-png",action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(create_preview(args.reconstruction_dir,static_png=not args.no_static_png),ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

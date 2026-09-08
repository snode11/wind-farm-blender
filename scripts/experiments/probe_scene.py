"""场景层 + 通道注册表的验收测试（纯离线，不起 FAST.Farm）。

跑：
    "C:\\Users\\s1155\\.conda\\envs\\wfcrl\\python.exe" scripts/experiments/probe_scene.py

验收点：
  1. 两份场景文件都能加载并通过校验
  2. 非法场景**必须**报错，且消息里带出错字段（否则 UI 没法提示用户）
  3. 场景能生成 WFCRL 的 FarmCase，坐标与 YAML 一致
  4. 传感器注册表能按场景实例化，保真度分类齐全
  5. 传感器能在假 ctx 上出数，形状对得上
  6. 抽稀按 rate_hz 生效
  7. 单个传感器抛异常不会打断整场（错误进通道而非冒泡）
"""
import os
import sys
import traceback

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from wfrl.scene import SceneError, load_scene, dump_scene           # noqa: E402
from wfrl.scene.schema import from_dict                             # noqa: E402
from wfrl.channels import Fidelity, registry                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FAILS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def main():
    print("== 1. 加载场景文件 ==")
    scenes = {}
    for fn in ("turb3_row.yaml", "turb6_grid.yaml"):
        sc = load_scene(os.path.join(ROOT, "scenes", fn))
        scenes[fn] = sc
        print(f"  {fn}: {sc.name} n={sc.n} backend={sc.backend} "
              f"sensors={[s.type for s in sc.sensors]}")
    check("两份场景都加载", len(scenes) == 2)
    check("turb3 是 3 台", scenes["turb3_row.yaml"].n == 3)
    check("turb6 是 6 台", scenes["turb6_grid.yaml"].n == 6)
    sc3 = scenes["turb3_row.yaml"]
    print("  局限说明:", *[f"\n    - {x}" for x in sc3.notes], sep="")
    check("局限说明非空", len(sc3.notes) >= 2)

    print("\n== 2. 非法场景必须报错 ==")
    base = sc3.to_dict()

    def bad(mut, why):
        d = {k: (v.copy() if isinstance(v, (dict, list)) else v)
             for k, v in base.items()}
        d["layout"] = [dict(t) for t in base["layout"]]
        d["inflow"] = dict(base["inflow"])
        d["sensors"] = [dict(s) for s in base["sensors"]]
        mut(d)
        try:
            from_dict(d)
        except SceneError as e:
            check(why, True, str(e)[:70])
            return
        except Exception as e:                                  # noqa: BLE001
            check(why, False, f"抛的不是 SceneError 而是 {type(e).__name__}: {e}")
            return
        check(why, False, "居然没报错")

    bad(lambda d: d.update(backend="isaac"), "未知后端")
    bad(lambda d: d["layout"].__setitem__(1, {"id": "T2", "x": 30.0, "y": 0.0}),
        "机组间距过近")
    bad(lambda d: d["layout"].__setitem__(1, {"id": "T1", "x": 504.0, "y": 0.0}),
        "机组 id 重复")
    bad(lambda d: d["inflow"].update(speed=99.0), "风速越界")
    bad(lambda d: d["inflow"].update(direction=400.0), "风向越界")
    bad(lambda d: d.update(controls=["yaww"]), "未知控制量")
    bad(lambda d: d["sensors"].append({"type": "mmwave"}), "未知传感器")
    bad(lambda d: d["sensors"].append({"type": "lidar", "turbines": "T9"}),
        "传感器指向不存在的机组")
    # YAML 1.1 的 on/off/yes/no 陷阱：键被解析成布尔。必须报错而不是静默挂全场。
    bad(lambda d: d["sensors"].append({"type": "lidar", True: "T1"}),
        "on: 被解析成布尔键")
    bad(lambda d: d["sensors"].append({"type": "lidar", "onn": "T1"}),
        "传感器未知字段")
    bad(lambda d: d.update(layout=[]), "空布局")
    bad(lambda d: (d.update(backend="floris"),
                   d["inflow"].update(turbulence="x.bts")),
        "floris 不支持湍流盒")

    print("\n== 3. 场景 → FarmCase ==")
    case = sc3.make_case(max_iter=64)
    check("坐标透传", list(case.xcoords) == sc3.xcoords, f"{case.xcoords}")
    check("机组数一致", case.num_turbines == sc3.n)
    check("dt 一致", int(case.dt) == int(sc3.dt))
    check("max_iter 透传", case.max_iter == 64)
    case6 = scenes["turb6_grid.yaml"].make_case()
    check("6 机布局是二维的", len(set(case6.ycoords)) == 2, f"{sorted(set(case6.ycoords))}")

    print("\n== 4. 传感器注册表 ==")
    print("  已注册:")
    for t, f, p in registry.describe():
        print(f"    {t:12s} {f:4s}  {p[:60]}")
    fids = {f for _, f, _ in registry.describe()}
    check("三个保真度都有实现", fids == {f.value for f in Fidelity}, f"{sorted(fids)}")
    check("provenance 都非空",
          all(p for _, _, p in registry.describe()))

    sensors = registry.build(sc3)
    check("按场景实例化", len(sensors) == len(sc3.sensors))
    lidar = [s for s in sensors if s.type_name == "lidar"][0]
    check("lidar 只挂 T1", lidar.indices == [0], f"{lidar.indices}")
    print("  标签:", [s.label for s in sensors])

    print("\n== 5. 假 ctx 上出数 ==")
    n = sc3.n
    ctx = {
        "power": np.array([2.1, 1.6, 1.5]),
        "yaw": np.zeros(n), "pitch": np.zeros(n),
        "pitch_meas": np.zeros(n), "torque": np.full(n, 4.0e4),
        "m_flap": np.random.RandomState(0).normal(6e3, 3e2, (n, 3)),
        "m_edge": np.random.RandomState(1).normal(4e3, 2e2, (n, 3)),
        "u_inf": 8.0, "t": 12.0, "_source": None,
    }
    topics = {}
    for s in sensors:
        out = s.step(ctx)
        check(f"{s.type_name} 出数", out is not None)
        if out:
            for k, v in out.items():
                topics[k] = v
                check(f"  {k} 无 error 键",
                      not (isinstance(v, dict) and "error" in v),
                      str(v)[:60] if isinstance(v, dict) and "error" in v else "")
    check("power 形状", np.shape(topics["power"]) == (3,), str(np.shape(topics["power"])))
    check("m_flap 形状", np.shape(topics["m_flap"]) == (3, 3))
    check("rotorspeed 有限", np.all(np.isfinite(topics["rotorspeed"])),
          f"{np.round(topics['rotorspeed'], 2)}")
    # 8 m/s、额定 12.1 rpm ⇒ 反解值必须落在额定以下的合理区间，否则是漏了齿轮箱速比
    check("rotorspeed 在额定量级", bool(np.all(topics["rotorspeed"] < 13.0)),
          f"max={topics['rotorspeed'].max():.2f} rpm")
    lid = topics["lidar"]
    npts = lidar.N_BEAM * len(lidar.RANGES)
    check("lidar 点云形状", np.shape(lid["points"]) == (1, npts, 3),
          str(np.shape(lid["points"])))
    check("lidar 风速形状", np.shape(lid["u"]) == (1, npts))
    check("lidar 风速在合理区间",
          bool(np.all(lid["u"] > 3) and np.all(lid["u"] < 20)),
          f"{lid['u'].min():.2f}~{lid['u'].max():.2f}")
    check("lidar 标注了无湍流盒", lid["has_turbulence"] is False)
    # 无湍流盒时 TI=0 ⇒ 视线速度只剩切变，同高度的点必须相等
    z = lid["points"][0][:, 2]
    same_z = np.abs(z - z[0]) < 1e-9
    check("无湍流盒时同高度视线速度一致",
          float(np.ptp(lid["u"][0][same_z])) < 1e-9)

    print("\n== 6. 抽稀 ==")
    # dt=3 s ⇒ 采样周期(步) = ceil(1/(rate*3))，下限 1。
    #   acoustic 0.2 Hz → ceil(1.67) = 2 步一次
    #   camera   0.5 Hz → ceil(0.67) = 1 步（比控制步慢的名义速率仍每步出，
    #            因为控制步 3 s 已经比 2 s 的采样间隔长）
    from wfrl.scene.schema import SensorSpec
    ac = registry.get("acoustic")(sc3, SensorSpec(type="acoustic"), [0, 1, 2])
    hits = [ac.step(ctx) is not None for _ in range(6)]
    check("acoustic 每 2 步出一次", hits == [True, False] * 3, str(hits))
    cam = registry.get("camera")(sc3, SensorSpec(type="camera"), [0])
    hits = [cam.step(ctx) is not None for _ in range(4)]
    check("camera 周期 1 步（dt 已比采样间隔长）", all(hits), str(hits))
    # 真正会被抽稀的：比 dt 慢得多的速率
    slow = registry.get("acoustic")(sc3, SensorSpec(type="acoustic"), [0])
    slow.rate_hz = 0.05                      # 20 s 周期 ⇒ ceil(20/3) = 7 步
    hits = [slow.step(ctx) is not None for _ in range(15)]
    check("0.05Hz ⇒ 7 步一次", sum(hits) == 3 and hits[0] and hits[7],
          f"{sum(hits)} 次 @ {[i for i,h in enumerate(hits) if h]}")

    print("\n== 7. 传感器异常不打断 ==")
    class Boom(type(sensors[0])):
        type_name = "boom"
        def sample(self, ctx):
            raise RuntimeError("故意炸")
    b = Boom(sc3, SensorSpec(type="power"), [0])
    out = b.step(ctx)
    check("异常被捕获成通道数据", isinstance(out, dict) and "error" in list(out.values())[0],
          str(out)[:60])

    print("\n== 8. 往返序列化 ==")
    tmp = os.path.join(ROOT, "results", "runs", "_scene_roundtrip.yaml")
    dump_scene(sc3, tmp)
    back = load_scene(tmp)
    check("往返后布局一致", back.xcoords == sc3.xcoords)
    check("往返后传感器一致",
          [s.type for s in back.sensors] == [s.type for s in sc3.sensors])
    os.remove(tmp)

    print()
    if FAILS:
        print(f"SCENE_FAILED — {len(FAILS)} 项: {FAILS}")
        return 1
    print("SCENE_OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                          # noqa: BLE001
        traceback.print_exc()
        print("SCENE_FAILED — 未捕获异常")
        sys.exit(1)

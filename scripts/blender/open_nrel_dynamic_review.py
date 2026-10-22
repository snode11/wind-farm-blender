"""Standalone estimate-only Blender window; no installed add-on or source replay.

Launch in a fresh Blender profile with WFRL_DYNAMIC_REVIEW_PACKAGE.  The three
original movies and frozen four-frame estimates share a controlled main-thread
player. Frame handlers only request work; they never change geometry.
"""
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Quaternion, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("nrel_dynamic_review_reader", ROOT / "wfrl/nrel_reconstruction/dynamic_review.py")
reader_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reader_module
spec.loader.exec_module(reader_module)
ReviewPackage = reader_module.ReviewPackage

ACTIVE = {"package": None, "value": None, "error": "Not loaded", "movies": {}, "objects": {},
          "movie_areas": {}, "view": None, "layout_steps": 0, "ready": False, "geometry_visible": False,
          "pending": False, "last_applied_frame": None, "load_request": None, "checks_complete": False,
          "focus_requested": False, "request_identity": None, "request_revision": 0,
          "generation": 0, "playing": False, "next_frame_due": None, "playback_error": "",
          "commit_id": 0, "commit_events": [], "commit_observers": [], "playback_samples": [],
          "request_reason": "initialization", "applying": False, "setting_frame": False,
          "draw_ack": {}, "draw_events": []}
DEFAULT_PATH = os.environ.get("WFRL_DYNAMIC_REVIEW_PACKAGE", "")
HUD_HANDLE = None
MOVIE_DRAW_HANDLE = None
PLAYBACK_KEYMAPS = []


def material(name, color):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1)
    return result


def curve(name, color, thickness=.05):
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = thickness
    data.bevel_resolution = 1
    data.materials.append(material(name, color))
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def set_curve(obj, coordinates):
    if len(coordinates) < 2:
        return
    if len(obj.data.splines) == 0:
        spline = obj.data.splines.new("POLY")
        spline.points.add(len(coordinates) - 1)
    else:
        spline = obj.data.splines[0]
        if len(spline.points) != len(coordinates):
            raise ValueError("Review curve topology must stay fixed while playing")
    spline.points.foreach_set("co", [value for point in coordinates for value in (*point, 1.)])


def marker(name, color):
    # Create an unselected data-block directly. A reload must not leave an
    # operator's active/selected object pointing at a deleted marker.
    segments, rings, radius = 12, 6, .22
    vertices = [(0., 0., radius)]
    for ring in range(1, rings):
        phi = math.pi * ring / rings
        vertices.extend((radius * math.sin(phi) * math.cos(2 * math.pi * i / segments),
                         radius * math.sin(phi) * math.sin(2 * math.pi * i / segments),
                         radius * math.cos(phi)) for i in range(segments))
    south = len(vertices)
    vertices.append((0., 0., -radius))
    faces = [(0, 1 + i, 1 + (i + 1) % segments) for i in range(segments)]
    for ring in range(rings - 2):
        first, next_ring = 1 + ring * segments, 1 + (ring + 1) * segments
        faces.extend((first + i, next_ring + i, next_ring + (i + 1) % segments,
                      first + (i + 1) % segments) for i in range(segments))
    last = 1 + (rings - 2) * segments
    faces.extend((last + i, south, last + (i + 1) % segments) for i in range(segments))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.materials.append(material(name, color))
    return obj


def clear_readout(reason):
    playback_pause()
    ACTIVE.update(package=None, value=None, error=str(reason), geometry_visible=False,
                  pending=False, draw_ack={})
    # A failed replacement must not keep presenting movies from the old
    # signed package while its geometry and readout say unavailable.
    for area in ACTIVE["movie_areas"].values():
        area.spaces.active.image = None
    for image in ACTIVE["movies"].values():
        bpy.data.images.remove(image)
    ACTIVE["movies"] = {}
    bpy.context.view_layer.objects.active = None
    for obj in list(ACTIVE["objects"].values()):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
    ACTIVE["objects"] = {}
    ACTIVE["last_applied_frame"] = None
    ACTIVE["request_identity"] = None
    for area in bpy.context.screen.areas:
        area.tag_redraw()


def rebuild_estimate_objects(package):
    for obj in list(ACTIVE["objects"].values()):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
    mesh = bpy.data.meshes.new("NREL.Review.Estimate")
    mesh.from_pydata(package.geometry["G3_vertices_world"][0].tolist(), [], package.geometry["faces"].tolist())
    mesh.update()
    obj = bpy.data.objects.new("NREL.Review.Estimate", mesh)
    bpy.context.scene.collection.objects.link(obj)
    mesh.materials.append(material("NREL.Review.Surface", (.55, .68, .81)))
    ACTIVE["objects"] = {"surface": obj}
    ACTIVE["objects"]["point"] = marker("NREL.Review.PriorPoint", (1., .28, .1))
    ACTIVE["objects"]["foot"] = marker("NREL.Review.PlaneFoot", (.1, .9, .5))
    ACTIVE["objects"]["perpendicular"] = curve("NREL.Review.SignedDistance", (1., .65, .1), .065)
    ACTIVE["objects"]["plane"] = curve("NREL.Review.IdealPlanePatch", (.1, .7, 1.), .035)
    ACTIVE["objects"]["trajectory"] = curve("NREL.Review.OfflinePointPath", (1., .3, .1), .035)
    # Allocate spline topology only at package load, outside frame handlers.
    for key, count in (("perpendicular", 2), ("plane", 5), ("trajectory", 4)):
        set_curve(ACTIVE["objects"][key], [(0., 0., 0.)] * count)
    for item in ACTIVE["objects"].values():
        item.hide_select = True
        item["scope"] = "REVIEW_ONLY; prior-defined point; DY1 not passed"
        # An external timeline seek can draw before the timer applies it.
        # Native depsgraph evaluation hides the previously committed geometry
        # without any Python RNA mutation from a frame or draw handler.
        item["nrel_review_applied_frame"] = -1
        item["nrel_review_applied_group"] = -1
        driver = item.driver_add("hide_viewport").driver
        driver.expression = "frame != applied_frame or group != applied_group"
        for name, owner, path in (
                ("applied_frame", item, '["nrel_review_applied_frame"]'),
                ("applied_group", item, '["nrel_review_applied_group"]'),
                ("group", bpy.context.scene, "nrel_review_group")):
            variable = driver.variables.new()
            variable.name, variable.type = name, "SINGLE_PROP"
            variable.targets[0].id_type = "SCENE" if owner == bpy.context.scene else "OBJECT"
            variable.targets[0].id = owner
            variable.targets[0].data_path = path


def load_package(directory):
    # Call only during initialization or a main-thread timer, never from a
    # frame/depsgraph handler. Flush pending evaluated copies before replacing
    # datablocks, then evaluate the new stable topology before any later seek.
    playback_pause()
    stop_native_animation()
    bpy.context.view_layer.update()
    # Clear first: an invalid replacement cannot leave previous measurements.
    clear_readout("Loading package")
    movies = {}
    try:
        package = ReviewPackage.load(directory)
        for camera in ("C1", "C2", "C3"):
            image = bpy.data.images.load(package.manifest["videos"][camera]["path"], check_existing=False)
            if image.source != "MOVIE":
                bpy.data.images.remove(image)
                raise ValueError(camera + " original input did not load as a movie")
            if image.frame_duration != 100:
                bpy.data.images.remove(image)
                raise ValueError(camera + " movie does not contain the original 100 frames")
            movies[camera] = image
        ACTIVE["movies"] = movies
        movies = None  # Ownership transferred to clear_readout.
        rebuild_estimate_objects(package)
        ACTIVE.update(package=package, error="", generation=ACTIVE["generation"] + 1)
        assign_movies()
        refresh(bpy.context.scene)
        bpy.context.view_layer.update()
        return True
    except Exception as exc:
        if movies is not None:
            for image in movies.values():
                bpy.data.images.remove(image)
        clear_readout("Package unavailable: " + str(exc))
        return False


def assign_movies():
    for camera, area in ACTIVE["movie_areas"].items():
        space = area.spaces.active
        image = ACTIVE["movies"].get(camera)
        if image is None:
            continue
        space.image = image
        space.use_image_pin = True
        user = space.image_user
        user.frame_start = 1
        user.frame_duration = 100
        user.frame_offset = 0
        # ND_FRAME can still recalculate this RNA value natively. Therefore
        # disabling auto-refresh alone is not our synchronization mechanism:
        # the controlled player commits frame_set + all display data together.
        user.use_auto_refresh = False
        user.use_cyclic = False


def stop_native_animation():
    """Cancel Blender's independent wall clock, in its own window context."""
    stopped = False
    for window in bpy.context.window_manager.windows:
        if window.screen.is_animation_playing:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.screen.animation_cancel(restore_frame=False)
            stopped = True
    return stopped


def playback_pause():
    if ACTIVE["playing"]:
        ACTIVE["playback_samples"].append({"event": "pause", "monotonic_s": time.monotonic(),
                                           "scene_frame": bpy.context.scene.frame_current})
        del ACTIVE["playback_samples"][:-600]
    ACTIVE.update(playing=False, next_frame_due=None)


def controlled_frame_set(scene, frame):
    ACTIVE["setting_frame"] = True
    try:
        scene.frame_set(frame)
    finally:
        ACTIVE["setting_frame"] = False


def playback_start():
    stop_native_animation()
    if ACTIVE["package"] is None:
        return False
    scene = bpy.context.scene
    if scene.frame_current >= 100:
        ACTIVE["request_reason"] = "replay"
        controlled_frame_set(scene, 1)
        refresh(scene)
    ACTIVE.update(playing=True, next_frame_due=time.monotonic() + .05, playback_error="")
    ACTIVE["playback_samples"].append({"event": "play", "monotonic_s": time.monotonic(),
                                       "scene_frame": scene.frame_current})
    del ACTIVE["playback_samples"][:-600]
    return True


def playback_toggle():
    stop_native_animation()
    if ACTIVE["playing"]:
        playback_pause()
    else:
        playback_start()
    return ACTIVE["playing"]


def playback_snapshot():
    """Read-only identity evidence; RNA requests do not prove decoded pixels."""
    scene = bpy.context.scene
    identity = ACTIVE["last_applied_frame"]
    value = displayed_value(scene)
    effective_hidden = {
        key: bool(obj.hide_get() or obj.hide_viewport)
        for key, obj in ACTIVE["objects"].items()}
    return {"scene_frame": scene.frame_current, "group": scene.nrel_review_group,
            "applied_identity": identity, "hud_identity": identity if not ACTIVE["pending"] else None,
            "commit_id": ACTIVE["commit_id"], "generation": ACTIVE["generation"],
            "draw_ack": dict(ACTIVE["draw_ack"]),
            "pending": ACTIVE["pending"], "playing": ACTIVE["playing"],
            "geometry_visible": ACTIVE["geometry_visible"], "geometry_hidden": effective_hidden,
            "geometry_suppressed_pending": all(effective_hidden.values()),
            "geometry_identities": {
                key: (obj["nrel_review_applied_frame"], obj["nrel_review_applied_group"])
                for key, obj in ACTIVE["objects"].items()},
            "movie_frames": {
                camera: area.spaces.active.image_user.frame_current
                for camera, area in ACTIVE["movie_areas"].items()},
            "value_frame": value["record"]["frame_id"] + 1 if value is not None else None,
            "value_group": identity[1] if value is not None else None,
            "native_playing": any(window.screen.is_animation_playing
                                  for window in bpy.context.window_manager.windows)}


def display_step_drawn():
    if bpy.app.background or not ACTIVE["ready"]:
        return True
    expected = {"view", *ACTIVE["movie_areas"]}
    return all(ACTIVE["draw_ack"].get(key) == ACTIVE["commit_id"] for key in expected)


def record_draw():
    """Observe completed editor drawing; never mutate Blender from a draw."""
    area = bpy.context.area
    key = "view" if area == ACTIVE["view"] else next(
        (camera for camera, editor in ACTIVE["movie_areas"].items() if editor == area), None)
    identity = ACTIVE["last_applied_frame"]
    if (key is None or identity is None or ACTIVE["pending"]
            or identity != (bpy.context.scene.frame_current, bpy.context.scene.nrel_review_group)):
        return
    movies = {camera: editor.spaces.active.image_user.frame_current
              for camera, editor in ACTIVE["movie_areas"].items()}
    if any(frame != identity[0] for frame in movies.values()):
        return
    if ACTIVE["draw_ack"].get(key) != ACTIVE["commit_id"]:
        ACTIVE["draw_ack"][key] = ACTIVE["commit_id"]
        ACTIVE["draw_events"].append({"editor": key, "commit_id": ACTIVE["commit_id"],
                                      "scene_frame": identity[0], "group": identity[1],
                                      "movie_frames": movies, "monotonic_s": time.monotonic()})
        del ACTIVE["draw_events"][:-1500]


def group_changed(scene, _context):
    playback_pause()
    stop_native_animation()
    ACTIVE["request_reason"] = "group"
    refresh(scene)


def refresh(scene, _depsgraph=None):
    """Frame/group notification only: no Blender RNA or geometry writes."""
    if ACTIVE["playing"] and not ACTIVE["setting_frame"] and not ACTIVE["applying"]:
        playback_pause()
        ACTIVE["request_reason"] = "external seek"
    identity = (scene.frame_current, scene.nrel_review_group)
    if (not ACTIVE["pending"] and identity == ACTIVE["last_applied_frame"]
            and ACTIVE["load_request"] is None):
        return
    if not ACTIVE["pending"] or identity != ACTIVE["request_identity"]:
        ACTIVE["request_revision"] += 1
    ACTIVE["request_identity"] = identity
    ACTIVE["pending"] = True
    ACTIVE["geometry_visible"] = False


def apply_pending():
    """One event-loop transaction: frame, movies, geometry, then readout."""
    # Space is remapped and the panel uses our operator. A native timeline
    # playback button remains reachable; cancel its independent wall clock and
    # fulfill its play request with the same controlled player.
    native_playing = stop_native_animation()
    if native_playing:
        ACTIVE["playback_samples"].append({"event": "native_cancel", "monotonic_s": time.monotonic(),
                                           "scene_frame": bpy.context.scene.frame_current})
        del ACTIVE["playback_samples"][:-600]
        if ACTIVE["package"] is not None:
            playback_start()
    request = ACTIVE["load_request"]
    if request is not None:
        ACTIVE["load_request"] = None
        load_package(request)
    scene = bpy.context.scene
    now = time.monotonic()
    if (ACTIVE["playing"] and not ACTIVE["pending"] and now >= ACTIVE["next_frame_due"]
            and display_step_drawn()):
        if scene.frame_current >= 100:
            playback_pause()
        else:
            ACTIVE["request_reason"] = "playback"
            controlled_frame_set(scene, scene.frame_current + 1)
    if not ACTIVE["pending"]:
        return .01
    frame = min(100, max(1, scene.frame_current))
    if frame != scene.frame_current:
        controlled_frame_set(scene, frame)
    identity = (frame, scene.nrel_review_group)
    revision, generation = ACTIVE["request_revision"], ACTIVE["generation"]
    started = time.monotonic()
    ACTIVE["applying"] = True
    try:
        value = apply_refresh(scene, identity)
        bpy.context.view_layer.update()
        if (identity != (scene.frame_current, scene.nrel_review_group)
                or revision != ACTIVE["request_revision"] or generation != ACTIVE["generation"]):
            # No previous measurement is published under a changed request.
            return .01
        ACTIVE.update(value=value, geometry_visible=value is not None,
                      last_applied_frame=identity, pending=False, draw_ack={})
        ACTIVE["commit_id"] += 1
        event = {"commit_id": ACTIVE["commit_id"], "monotonic_s": time.monotonic(),
                 "elapsed_ms": (time.monotonic() - started) * 1000,
                 "scene_frame": frame, "group": identity[1], "generation": generation,
                 "reason": ACTIVE["request_reason"]}
        ACTIVE["commit_events"].append(event)
        del ACTIVE["commit_events"][:-600]
        for observer in tuple(ACTIVE["commit_observers"]):
            observer(dict(event))
        if ACTIVE["playing"]:
            # No skipped estimates or wall-clock catch-up: slow decoding draws
            # slow the player. The target interval is separately recorded.
            fps = scene.render.fps / scene.render.fps_base
            ACTIVE["next_frame_due"] = event["monotonic_s"] + 1. / max(1., fps)
            if frame == 100:
                playback_pause()
        if ACTIVE["focus_requested"] and ACTIVE["value"] is not None:
            ACTIVE["focus_requested"] = False
            focus_tip()
    except Exception as exc:
        ACTIVE["playback_error"] = str(exc)
        clear_readout("Review update unavailable: " + str(exc))
        print("NREL_DYNAMIC_REVIEW_UPDATE_ERROR", str(exc), flush=True)
    finally:
        ACTIVE["applying"] = False
    return .01


def apply_refresh(scene, identity):
    frame, group = identity
    for area in ACTIVE["movie_areas"].values():
        area.spaces.active.image_user.frame_current = frame
        area.tag_redraw()
    package = ACTIVE["package"]
    value = package.at_scene_frame(frame, group) if package else None
    for obj in ACTIVE["objects"].values():
        obj.hide_set(value is None)
    if value is not None:
        row = value["record"]
        objects = ACTIVE["objects"]
        objects["surface"].data.vertices.foreach_set("co", value["vertices_world"].astype(np.float32).ravel())
        objects["surface"].data.update()
        point, foot = np.asarray(row["point_world_m"]), np.asarray(row["foot_world_m"])
        objects["point"].location = Vector(point)
        objects["foot"].location = Vector(foot)
        set_curve(objects["perpendicular"], [point, foot])
        normal = np.asarray(row["normal_world_unit"])
        axis = np.array([0., 0., 1.]) if abs(normal[2]) < .9 else np.array([0., 1., 0.])
        u = np.cross(normal, axis); u /= np.linalg.norm(u)
        v = np.cross(normal, u)
        corners = [foot + 6 * (a * u + b * v) for a, b in [(-1,-1), (1,-1), (1,1), (-1,1), (-1,-1)]]
        set_curve(objects["plane"], corners)
        # The complete four-frame path is explicitly an offline review layer.
        set_curve(objects["trajectory"], [r["point_world_m"] for r in package.sequence["groups"][group]["records"]])
    for obj in ACTIVE["objects"].values():
        obj["nrel_review_applied_frame"] = frame
        obj["nrel_review_applied_group"] = ("G1", "G2", "G3").index(group)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return value


def focus_tip():
    area = ACTIVE["view"]
    value = displayed_value(bpy.context.scene)
    if area is None or value is None:
        return
    row = value["record"]
    region = area.spaces.active.region_3d
    region.view_location = Vector((np.asarray(row["point_world_m"]) + row["foot_world_m"]) * .5)
    region.view_distance = 14
    region.view_perspective = "ORTHO"
    region.view_rotation = Quaternion((.820473, .424708, -.175920, -.339851))
    area.tag_redraw()


def displayed_value(scene):
    """Never label an older estimate with a newly selected frame or group."""
    if ACTIVE["pending"] or ACTIVE["last_applied_frame"] != (scene.frame_current, scene.nrel_review_group):
        return None
    return ACTIVE["value"]


class NREL_OT_seek(bpy.types.Operator):
    bl_idname = "nrel_review.seek"
    bl_label = "Seek original video frame"
    frame: bpy.props.IntProperty(default=44)
    def execute(self, context):
        playback_pause()
        stop_native_animation()
        ACTIVE["request_reason"] = "seek"
        context.scene.frame_set(min(100, max(1, self.frame)))
        refresh(context.scene)
        return {"FINISHED"}


class NREL_OT_step(bpy.types.Operator):
    bl_idname = "nrel_review.step"
    bl_label = "Step original movie frame"
    delta: bpy.props.IntProperty(default=1)
    def execute(self, context):
        playback_pause()
        stop_native_animation()
        ACTIVE["request_reason"] = "step"
        context.scene.frame_set(min(100, max(1, context.scene.frame_current + self.delta)))
        refresh(context.scene)
        return {"FINISHED"}


class NREL_OT_reload(bpy.types.Operator):
    bl_idname = "nrel_review.reload"
    bl_label = "Reload signed review package"
    def execute(self, context):
        playback_pause()
        stop_native_animation()
        ACTIVE["request_reason"] = "reload"
        # A same-frame replacement still changes data identity. This operator
        # runs outside frame/draw handlers, so invalidate the old visibility
        # stamp before the timer validates and replaces the signed package.
        for obj in ACTIVE["objects"].values():
            obj["nrel_review_applied_frame"] = -1
            # ID-property changes alone did not reevaluate hide_viewport in
            # Blender 5.2's same-frame reload draw. This safe operator also
            # hides the view-layer base directly; the loaded package's next
            # transaction restores availability with hide_set(value is None).
            obj.hide_set(True)
        ACTIVE["load_request"] = context.scene.nrel_review_path
        refresh(context.scene)
        context.view_layer.update()
        return {"FINISHED"}


class NREL_OT_play_pause(bpy.types.Operator):
    bl_idname = "nrel_review.play_pause"
    bl_label = "Play / pause shared review frames"
    def execute(self, context):
        if ACTIVE["package"] is None:
            self.report({"WARNING"}, "Load a valid signed review package first")
            return {"CANCELLED"}
        playback_toggle()
        for area in context.screen.areas:
            area.tag_redraw()
        return {"FINISHED"}


class NREL_OT_focus(bpy.types.Operator):
    bl_idname = "nrel_review.focus"
    bl_label = "Focus point and foot"
    def execute(self, context):
        focus_tip()
        return {"FINISHED"}


class NREL_PT_review(bpy.types.Panel):
    bl_label = "Dynamic research review"
    bl_idname = "NREL_PT_dynamic_review"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    # Blender 5.2 exposes category RNA but rejects assignment at runtime.
    # Factory startup opens Tool; place the review first in that default tab.
    bl_category = "Tool"
    bl_order = -100
    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text="REVIEW_ONLY", icon="ERROR")
        layout.label(text="DY1 NOT PASSED")
        layout.label(text="Prior-defined structural point")
        layout.label(text="Ideal shared rigid plane")
        layout.prop(scene, "nrel_review_group", text="Group")
        layout.prop(scene, "frame_current", text="Scene frame")
        row = layout.row(align=True)
        row.operator("nrel_review.step", text="Back").delta = -1
        row.operator("nrel_review.play_pause", text="Pause" if ACTIVE["playing"] else "Play")
        row.operator("nrel_review.step", text="Next").delta = 1
        row = layout.row(align=True)
        for original in (42,43,44,45):
            row.operator("nrel_review.seek", text=str(original)).frame = original + 1
        layout.label(text="Buttons use original movie indices")
        layout.operator("nrel_review.focus")
        layout.separator()
        identity = ACTIVE["last_applied_frame"]
        frame = (identity[0] if identity and not ACTIVE["pending"] else scene.frame_current) - 1
        layout.label(text=f"Original frame {frame} / 99")
        layout.label(text=f"Sample center {reader_module.sample_time(frame):.3f} s")
        value = displayed_value(scene)
        if value is None:
            layout.label(text="d: unavailable; no interpolation")
            layout.label(text="UPDATING SHARED FRAME" if ACTIVE["pending"] else ACTIVE["error"] or "Outside four saved estimate frames", icon="INFO")
        else:
            record = value["record"]
            layout.label(text=f"d = {record['d_m']:+.5f} m")
            layout.label(text="Image-grid range")
            layout.label(text="Not a joint uncertainty interval")
            interval = record["range_m"]
            layout.label(text="unavailable" if interval is None else f"[{interval[0]:+.4f}, {interval[1]:+.4f}] m")
            layout.label(text=record["status"])
        layout.label(text="Offline point path uses all 4 frames")
        layout.label(text="Shared steps; slow draws reduce playback speed")
        layout.prop(scene, "nrel_review_path", text="Package")
        layout.operator("nrel_review.reload")


def review_ui_scale():
    # POST_PIXEL coordinates are physical pixels on Retina displays. Native
    # UI widgets scale themselves; BLF text and our plot coordinates do not.
    system = bpy.context.preferences.system
    return max(1., system.ui_scale, system.pixel_size)


def hud_bounds():
    """Keep custom drawing out of overlapping native headers and sidebar."""
    region, area = bpy.context.region, bpy.context.area
    width, height = region.width, region.height
    for other in area.regions:
        if other.type == "UI" and area.spaces.active.show_region_ui and other.width > 1:
            # Region overlap can leave WINDOW width equal to the whole area.
            width = min(width, max(0, other.x - region.x))
        elif other.type in {"HEADER", "TOOL_HEADER"} and other.height > 1:
            if region.y < other.y < region.y + height:
                height = min(height, other.y - region.y)
    return width, height


def wrap_hud_line(line, available_width):
    import blf
    rows, row = [], ""
    for word in line.split():
        candidate = (row + " " + word).strip()
        if row and blf.dimensions(0, candidate)[0] > available_width:
            rows.append(row)
            row = ""
        # Status identifiers have no spaces; keep them inside the same bounds
        # as prose instead of letting them extend beneath the native sidebar.
        while len(word) > 1 and blf.dimensions(0, word)[0] > available_width:
            cut = 1
            while cut < len(word) and blf.dimensions(0, word[:cut + 1])[0] <= available_width:
                cut += 1
            rows.append(word[:cut])
            word = word[cut:]
        row = (row + " " + word).strip()
    return rows + [row]


def draw_hud():
    if bpy.context.area != ACTIVE["view"]:
        return
    record_draw()
    import blf, gpu
    from gpu_extras.batch import batch_for_shader
    width, height = hud_bounds()
    scale = review_ui_scale()
    inset, spacing = 12 * scale, 21 * scale
    lines = ["REVIEW_ONLY | DY1 NOT PASSED"]
    value = displayed_value(bpy.context.scene)
    scene = bpy.context.scene
    identity = ACTIVE["last_applied_frame"] if not ACTIVE["pending"] else None
    frame, group = (identity if identity else (scene.frame_current, scene.nrel_review_group))
    frame -= 1
    lines.append(f"{group} | Original frame {frame} / 99 | Sample center {reader_module.sample_time(frame):.3f} s")
    if value:
        row = value["record"]
        lines.append(f"d {row['d_m']:+.5f} m")
    else:
        lines.append("UPDATING SHARED FRAME | d unavailable" if ACTIVE["pending"] else "Estimate/distance UNAVAILABLE; no interpolation")
    lines.append("Prior-defined point / ideal shared plane")
    if value:
        lines.append(row["status"])
        interval = row["range_m"]
        lines.append("Image-grid d range: unavailable" if interval is None else f"Image-grid d range [{interval[0]:+.4f}, {interval[1]:+.4f}] m (not a joint interval)")
    lines.append("Orange: point/distance | Green: foot | Blue: plane")
    blf.size(0, 14 * scale)
    available_width = max(1., width - 2 * inset)
    lines = [row for line in lines for row in wrap_hud_line(line, available_width)]
    # Keep the scope, selected frame and d first when a resized viewport only
    # has space for a few rows. The full research readout remains in the panel.
    max_rows = max(0, int((height - 2 * inset) / spacing))
    if not max_rows or width < 32 * scale:
        return
    lines = lines[:max_rows]
    top = height - inset - 14 * scale
    bottom = top - (len(lines) - 1) * spacing - 7 * scale
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    try:
        shader.bind(); shader.uniform_float("color", (.015, .025, .04, .88))
        batch_for_shader(shader, "TRI_FAN", {"pos": [(inset / 2, bottom), (width - inset / 2, bottom),
                         (width - inset / 2, height - inset / 2), (inset / 2, height - inset / 2)]}).draw(shader)
    finally:
        gpu.state.blend_set("NONE")
    blf.color(0, 1., .84, .28, 1.)
    for index, line in enumerate(lines):
        blf.position(0, inset, top - index * spacing, 0)
        blf.draw(0, line)
    if ACTIVE["package"] is not None and not ACTIVE["pending"]:
        draw_distance_plot(width, bottom - 12 * scale, scale)


def draw_distance_plot(width, available_height, scale):
    """Offline four-sample d curve with explicitly independent image-grid bars."""
    import blf, gpu
    from gpu_extras.batch import batch_for_shader
    identity = ACTIVE["last_applied_frame"]
    if ACTIVE["pending"] or identity is None:
        return
    records = ACTIVE["package"].sequence["groups"][identity[1]]["records"]
    if width < 160 * scale:
        return
    x0, y0 = 22 * scale, 32 * scale
    chart_width = min(350 * scale, width - 45 * scale)
    blf.size(0, 12 * scale)
    titles = [row for line in ("Offline d (orange); image-grid ranges (blue)",
              "All 4 saved samples; no joint uncertainty interval") for row in wrap_hud_line(line, chart_width)]
    title_height = (27 + 15 * (len(titles) - 1)) * scale
    chart_height = min(86 * scale, available_height - y0 - title_height)
    if chart_height < 40 * scale:
        return
    values = [row["d_m"] for row in records]
    for row in records:
        if row["range_m"] is not None:
            values.extend(row["range_m"])
    low, high = min(values), max(values)
    margin = max(.02, (high - low) * .12)
    low, high = low - margin, high + margin
    def location(index, d):
        return (x0 + chart_width * index / 3, y0 + chart_height * (d - low) / (high - low))
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    def draw(mode, coordinates, color):
        shader.bind(); shader.uniform_float("color", color)
        batch_for_shader(shader, mode, {"pos": coordinates}).draw(shader)
    gpu.state.blend_set("ALPHA")
    try:
        draw("TRI_FAN", [(x0-10*scale,y0-20*scale), (x0+chart_width+10*scale,y0-20*scale), (x0+chart_width+10*scale,y0+chart_height+title_height), (x0-10*scale,y0+chart_height+title_height)], (.015,.025,.04,.88))
        draw("LINE_STRIP", [location(i, r["d_m"]) for i, r in enumerate(records)], (1.,.65,.1,1.))
        for i, row in enumerate(records):
            if row["range_m"] is not None:
                draw("LINES", [location(i,row["range_m"][0]), location(i,row["range_m"][1])], (.15,.72,1.,1.))
            if row["frame_id"] == identity[0] - 1:
                x, y = location(i,row["d_m"])
                draw("LINES", [(x-4*scale,y-4*scale),(x+4*scale,y+4*scale),(x-4*scale,y+4*scale),(x+4*scale,y-4*scale)], (1.,.3,.1,1.))
        blf.size(0, 12 * scale)
        blf.color(0, .9,.93,1.,1.)
        for i, title in enumerate(reversed(titles)):
            blf.position(0,x0,y0+chart_height+(8+15*i)*scale,0); blf.draw(0,title)
        for i, row in enumerate(records):
            label = f"{row['sim_time_s']:.3f}"
            label_width = blf.dimensions(0, label)[0]
            blf.position(0,max(12*scale, min(width-label_width-12*scale, location(i,row["d_m"])[0]-label_width/2)),y0-18*scale,0)
            blf.draw(0,label)
    finally:
        gpu.state.blend_set("NONE")


def prepare_layout():
    if bpy.app.background:
        ACTIVE["ready"] = True
        return None
    ACTIVE["layout_steps"] += 1
    if ACTIVE["layout_steps"] > 20:
        ACTIVE["error"] = "Native editor layout failed"
        return None
    screen = bpy.context.window.screen
    areas = [a for a in screen.areas if a.type in {"VIEW_3D", "IMAGE_EDITOR"}]
    if len(areas) < 4:
        area = max(areas, key=lambda a: a.width * a.height)
        with bpy.context.temp_override(window=bpy.context.window, area=area):
            bpy.ops.screen.area_split(direction="VERTICAL" if len(areas) == 1 else "HORIZONTAL", factor=.5)
        return .1
    areas.sort(key=lambda a: (-a.y, a.x))
    for camera, area in zip(("C1", "C2", "C3"), areas[:3]):
        area.type = "IMAGE_EDITOR"
        area.spaces.active.show_region_ui = False
        area.spaces.active.show_region_toolbar = False
        ACTIVE["movie_areas"][camera] = area
    area = areas[3]
    area.type = "VIEW_3D"
    ACTIVE["view"] = area
    space = area.spaces.active
    space.show_region_ui = True
    space.show_region_toolbar = False
    space.show_region_tool_header = False
    space.shading.type = "SOLID"
    space.shading.color_type = "MATERIAL"
    space.overlay.show_floor = False
    space.overlay.show_axis_x = False
    space.overlay.show_axis_y = False
    space.overlay.show_text = False
    assign_movies()
    refresh(bpy.context.scene)
    ACTIVE["focus_requested"] = True
    for area in ACTIVE["movie_areas"].values():
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
            bpy.ops.image.view_all(fit_view=True)
    ACTIVE["ready"] = True
    print("NREL_DYNAMIC_REVIEW_WINDOW_READY", flush=True)
    bpy.app.timers.register(after_ready, first_interval=.5)
    return None


def after_ready():
    if os.environ.get("WFRL_DYNAMIC_REVIEW_CHECK") == "1":
        check_spec = importlib.util.spec_from_file_location("nrel_dynamic_review_native_checks", ROOT / "scripts/blender/check_nrel_dynamic_review.py")
        checks = importlib.util.module_from_spec(check_spec)
        check_spec.loader.exec_module(checks)
        output = os.environ.get("WFRL_TEST_OUTPUT", str(ACTIVE["package"].path.parent / "window-check") if ACTIVE["package"] else "")
        checks.run_checks(viewer_interface(), output)
    else:
        save_review_blend()
    return None


def save_review_blend():
    save = os.environ.get("WFRL_DYNAMIC_REVIEW_SAVE")
    if save and ACTIVE["package"] is not None:
        path = Path(save).expanduser().resolve()
        if path.exists():
            raise ValueError("Refusing to overwrite an existing review blend")
        path.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True)
        print("NREL_DYNAMIC_REVIEW_SAVED", str(path), flush=True)
    return None


def viewer_interface():
    # Blender executes --python scripts in a temporary global namespace, then
    # restores __main__. Keep an explicit callable interface for timer checks.
    return SimpleNamespace(ACTIVE=ACTIVE, load_package=load_package, refresh=refresh, focus_tip=focus_tip,
                           apply_pending=apply_pending, save_review_blend=save_review_blend,
                           playback_start=playback_start, playback_pause=playback_pause,
                           playback_toggle=playback_toggle, playback_snapshot=playback_snapshot,
                           displayed_value=displayed_value, display_step_drawn=display_step_drawn)


def init():
    global HUD_HANDLE, MOVIE_DRAW_HANDLE
    for cls in (NREL_OT_seek, NREL_OT_step, NREL_OT_reload, NREL_OT_play_pause, NREL_OT_focus, NREL_PT_review):
        bpy.utils.register_class(cls)
    bpy.types.Scene.nrel_review_path = bpy.props.StringProperty(name="Review package", subtype="DIR_PATH", default=DEFAULT_PATH)
    bpy.types.Scene.nrel_review_group = bpy.props.EnumProperty(name="Research group", items=[
        ("G1", "G1: rigid prior", "Frozen reference with allowed rigid pose", 0, 0),
        ("G2", "G2: same prior, image off", "Same dynamic model and initialization", 0, 1),
        ("G3", "G3: video and prior", "Research hypothesis; no metric acceptance", 0, 2)], default="G3", update=group_changed)
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.frame_start, scene.frame_end = 1, 100
    scene.render.fps, scene.render.fps_base = 20, 1
    scene["nrel_review_timebase_fps"] = 20.
    scene["nrel_review_status"] = "REVIEW_ONLY / DY1_NOT_PASSED"
    bpy.app.driver_namespace["nrel_dynamic_review"] = viewer_interface()
    scene.frame_set(44)
    bpy.app.handlers.frame_change_post.append(refresh)
    load_package(DEFAULT_PATH)
    if not bpy.app.background:
        HUD_HANDLE = bpy.types.SpaceView3D.draw_handler_add(draw_hud, (), "WINDOW", "POST_PIXEL")
        MOVIE_DRAW_HANDLE = bpy.types.SpaceImageEditor.draw_handler_add(record_draw, (), "WINDOW", "POST_PIXEL")
        keyconfig = bpy.context.window_manager.keyconfigs.addon
        if keyconfig:
            # Blender 5.2's Frames keymap owns Space in movie, 3D, and timeline
            # editors. Native buttons are guarded
            # separately by apply_pending, since they bypass keymaps.
            keymap = keyconfig.keymaps.new(name="Frames", space_type="EMPTY")
            item = keymap.keymap_items.new("nrel_review.play_pause", "SPACE", "PRESS")
            PLAYBACK_KEYMAPS.append((keymap, item))
    if bpy.app.background:
        prepare_layout()
    else:
        bpy.app.timers.register(prepare_layout, first_interval=.2)
    bpy.app.timers.register(apply_pending, first_interval=.03)


if __name__ == "__main__":
    init()

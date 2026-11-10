"""Export the main-menu hallway door as a reusable Godot prop.

Run: blender -b main_menu_background_scene.blend --python export_main_menu_door.py

Output: deadline_zero/assets/models/ui/main_menu_door.gltf (+ .bin and shared textures)

The mesh is baked into its closed pose with the hinge at the origin, so a Godot
door scene can rotate the pivot to open and close it. The source hinge angle is
printed so the scene animation stays in sync with the Blender set.

The actual export is done by export_objects() in export_scene.py, which knows
nothing about this project; the paths below are what makes this script specific
to it.
"""
import math
import os
import sys

import bpy
from mathutils import Matrix

# ---------------------------------------------------------------------------
# EDIT THESE PATHS so they match your project. They are resolved relative to the folder holding BLEND_NAME (projects/<project>/ in this repo), so the export works on any machine. OUTPUT_PATH is the .gltf the Godot door scene imports; TEXTURE_ROOT is the shared texture tree that exported maps are reused from or added to. DOOR_ROOT is the object in the scene to bake.
# ---------------------------------------------------------------------------
BLEND_NAME: str = "main_menu_background_scene.blend"
OUTPUT_PATH: str = "../../output/assets/models/ui/main_menu_door.gltf"
TEXTURE_ROOT: str = "../../output/assets/textures/facility"
DOOR_ROOT: str = "kit_door.001"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_scene import _cleanup, _evaluated_mesh, export_objects  # noqa: E402

def _door_hinge_angle(door_root: bpy.types.Object) -> float:
	"""Total hinge opening angle, including the posing armature."""
	angle = door_root.matrix_world.to_euler().z
	for rig in (o for o in door_root.children_recursive if o.type == "ARMATURE"):
		for bone in rig.pose.bones:
			delta = bone.matrix @ bone.bone.matrix_local.inverted()
			bone_angle = delta.to_euler().z
			if abs(bone_angle) > abs(angle):
				angle = bone_angle
	return angle


def _rigs_under(obj: bpy.types.Object) -> list:
	return [o for o in obj.children_recursive if o.type == "ARMATURE"]

def export() -> str:
	if not bpy.data.filepath:
		raise RuntimeError("Save %s before exporting: paths resolve relative to the .blend." % BLEND_NAME)
	if BLEND_NAME not in os.path.basename(bpy.data.filepath):
		raise RuntimeError("Open %s before exporting this asset." % BLEND_NAME)
	if bpy.context.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")

	door_root = bpy.data.objects.get(DOOR_ROOT)
	if door_root is None:
		raise RuntimeError("Door root %s missing" % DOOR_ROOT)
	door_source = next((o for o in door_root.children_recursive if o.type == "MESH"), None)
	if door_source is None:
		raise RuntimeError("Door mesh missing under %s" % DOOR_ROOT)

	rigs = _rigs_under(door_root)
	rest_state = [(rig, rig.data.pose_position) for rig in rigs]
	try:
		hinge = door_root.matrix_world.translation.copy()
		object_angle = door_root.matrix_world.to_euler().z
		open_angle = _door_hinge_angle(door_root)
		# The rig is posed open in the scene. Bake the rest/closed pose; Godot
		# rotates the pivot by dz_open_angle_deg to reproduce the open pose.
		for rig in rigs:
			rig.data.pose_position = "REST"
		bpy.context.view_layer.update()
		mesh = _evaluated_mesh(door_source, "DoorMesh")
		mesh.data.transform(Matrix.Rotation(-object_angle, 4, "Z") @ Matrix.Translation(-hinge))
		mesh.data.update()
		mesh["dz_door_mesh"] = True
		mesh["dz_source"] = door_root.name
		mesh["dz_open_angle_deg"] = math.degrees(open_angle)
		export_objects(
			[mesh],
			OUTPUT_PATH,
			texture_root=TEXTURE_ROOT,
			vertex_color="MATERIAL",
			export_all_vertex_colors=True,
		)
		print("door hinge (Blender world):", tuple(round(v, 6) for v in hinge))
		print("door open angle degrees:", round(math.degrees(open_angle), 6))
		return OUTPUT_PATH
	finally:
		for rig, pose_position in rest_state:
			rig.data.pose_position = pose_position
		bpy.context.view_layer.update()
		_cleanup()

if __name__ == "__main__":
	export()

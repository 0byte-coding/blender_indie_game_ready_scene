"""Export the main-menu background scene collection to Godot.

Run: blender -b main_menu_background_scene.blend --python export_main_menu_background.py

This is the project-specific entry point. It holds nothing but the paths and
settings for this one asset and passes them to the generic exporter in
export_scene.py, which contains no project paths at all. Anything that should
stay out of the export is flagged in the blend with dz_export = False (for
example the door, which is exported separately by export_main_menu_door.py).

For any other project or scene either write a small entry point like this one or
call the generic exporter directly:
    blender -b scene.blend --python export_scene.py -- --output out.gltf --texture-root ../textures
"""
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export_scene import _cleanup, _evaluated_mesh, _select_tree, export_scene  # noqa: E402

__all__ = ["_cleanup", "_evaluated_mesh", "_select_tree", "export", "export_scene"]

# ---------------------------------------------------------------------------
# EDIT THESE PATHS so they match your project. They are resolved relative to the folder holding BLEND_NAME (projects/<project>/ in this repo), so the export works on any machine. OUTPUT_PATH is the .gltf Godot imports the menu background from; TEXTURE_ROOT is the shared texture tree that exported maps are reused from or added to.
# ---------------------------------------------------------------------------
BLEND_NAME: str = "main_menu_background_scene.blend"
COLLECTION: str = "scene"  # collection in the .blend to export
OUTPUT_PATH: str = "../../output/assets/models/ui/main_menu_background.gltf"
TEXTURE_ROOT: str = "../../output/assets/textures/facility"
# Name Godot sees for the imported root; scenes/ui/menu_background_scene.tscn
# refers to it by name, so keep the two in sync.
ROOT_NAME: str = "MenuBackground"

def export() -> str:
	if not bpy.data.filepath:
		raise RuntimeError("Save %s before exporting: paths resolve relative to the .blend." % BLEND_NAME)
	if BLEND_NAME not in os.path.basename(bpy.data.filepath):
		raise RuntimeError("Open %s before exporting this asset." % BLEND_NAME)
	return export_scene(
		collection=COLLECTION,
		output=OUTPUT_PATH,
		texture_root=TEXTURE_ROOT,
		root_name=ROOT_NAME,
	)


if __name__ == "__main__":
	export()

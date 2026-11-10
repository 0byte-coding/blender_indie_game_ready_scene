"""Export a Blender collection to Godot as glTF Separate.

Run:
    blender -b scene.blend --python export_scene.py -- [options]

Options:
    --collection NAME     Collection to export (default: scene)
    --output PATH         Output .gltf path, relative to the .blend
    --texture-root PATH   Texture tree to reuse, relative to the .blend
    --center              Centre the joined mesh on XZ and put its floor at Y=0
    --no-center           Do not centre the joined mesh
    --animations          Export animations (off by default)
    --root-name NAME      Name of the exported root node

Settings you do not pass in belong in the blend, not in this script. Put
these custom properties on the exported collection:

    dz_export_path   Output .gltf path, relative to the .blend
    dz_texture_root  Texture tree to reuse, relative to the .blend
    dz_center        Centre the joined mesh (boolean)
    dz_export_root   Root node name

Materials may set:

    dz_gltf_albedo      Image datablock to use as the glTF base colour
    dz_gltf_albedo_uv   UV map for that image (defaults to the first UV map)

Any object with dz_export=False is skipped together with all its children.
Only visible objects are exported. Visible meshes are baked (modifiers
applied) and joined into one Static mesh. Lights, cameras, empties and other
non-mesh objects are exported as separate nodes, so Godot can import them.

Textures are written next to the .gltf using relative URIs. If a texture with
the same filename already exists under --texture-root it is reused in place; new
textures keep the source asset folder layout, meaning everything below the lowest
"assets" folder of the source path (an image read from
<path>/assets/textures/brick/brick_001_diffuse_1k.webp is written to
<texture root>/textures/brick/brick_001_diffuse_1k.webp). Material
textures that glTF itself cannot reference (shader-only maps) are copied to
the same tree so Godot shaders can use them.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix, Vector

DEFAULT_COLLECTION: str = "scene"
ASSET_ANCHOR: str = "assets"
TEMP_COLLECTION: str = "__dz_export"
SKIP_PROPERTY: str = "dz_export"

_temp_objects: list = []
_temp_materials: list = []
_renamed: list = []
_renamed_materials: list = []


def _blend_dir() -> Path:
	"""Folder of the open .blend; relative paths are resolved against it."""
	path = bpy.data.filepath
	return Path(path).resolve().parent if path else Path.cwd()


def _resolve(path: str | Path, base: Path | None = None) -> Path:
	path = Path(path).expanduser()
	if not path.is_absolute():
		path = (base or _blend_dir()) / path
	return path.resolve()


def _temp_collection() -> bpy.types.Collection:
	col = bpy.data.collections.get(TEMP_COLLECTION)
	if col is None:
		col = bpy.data.collections.new(TEMP_COLLECTION)
	if col.name not in bpy.context.scene.collection.children:
		bpy.context.scene.collection.children.link(col)
	return col


def _link_temp(obj: bpy.types.Object) -> bpy.types.Object:
	_temp_collection().objects.link(obj)
	_temp_objects.append(obj)
	return obj


def _free_name(name: str) -> str:
	other = bpy.data.objects.get(name)
	if other is not None and other not in _temp_objects:
		_renamed.append((other, name))
		other.name = name + "__src"
	return name


def _free_material_name(name: str) -> str:
	other = bpy.data.materials.get(name)
	if other is not None and other not in _temp_materials:
		_renamed_materials.append((other, name))
		other.name = name + "__src"
	return name


def _export_material(material: bpy.types.Material, cache: dict) -> bpy.types.Material:
	"""Use a temporary material with an explicit glTF base-colour image when requested."""
	albedo = material.get("dz_gltf_albedo")
	if not albedo:
		return material
	key = material.as_pointer()
	if key in cache:
		return cache[key]
	image = bpy.data.images.get(str(albedo))
	if image is None:
		print("material", material.name, "missing image", albedo)
		return material
	copy = material.copy()
	copy.name = _free_material_name(material.name)
	_temp_materials.append(copy)
	if not copy.use_nodes:
		copy.use_nodes = True
	nodes = copy.node_tree.nodes
	links = copy.node_tree.links
	bsdf = next((node for node in nodes if node.type == "BSDF_PRINCIPLED"), None)
	if bsdf is None:
		print("material", material.name, "has no Principled BSDF")
		return copy
	base_color = bsdf.inputs["Base Color"]
	for link in list(links):
		if link.to_socket == base_color:
			links.remove(link)
	texture = nodes.new("ShaderNodeTexImage")
	texture.image = image
	uv_name = material.get("dz_gltf_albedo_uv")
	if uv_name:
		uv = nodes.new("ShaderNodeUVMap")
		uv.uv_map = str(uv_name)
		links.new(uv.outputs["UV"], texture.inputs["Vector"])
	links.new(texture.outputs["Color"], base_color)
	cache[key] = copy
	return copy


def _skip_tree(obj: bpy.types.Object) -> bool:
	current = obj
	while current is not None:
		if SKIP_PROPERTY in current and not bool(current[SKIP_PROPERTY]):
			return True
		current = current.parent
	return False


def _exported(obj: bpy.types.Object) -> bool:
	return not _skip_tree(obj) and obj.visible_get()


SUPPORTED_LIGHT_TYPES = {"POINT", "SPOT", "SUN"}


def _exportable_other(obj: bpy.types.Object) -> bool:
	"""Skip plain empties and light types glTF cannot represent."""
	if obj.type == "EMPTY" and not obj.keys() and obj.instance_type == "NONE":
		return False
	if obj.type == "LIGHT" and obj.data.type not in SUPPORTED_LIGHT_TYPES:
		return False
	return True


def _evaluated_mesh(obj: bpy.types.Object, name: str) -> bpy.types.Object:
	"""Copy the evaluated mesh at its world transform (modifiers included)."""
	depsgraph = bpy.context.evaluated_depsgraph_get()
	mesh = bpy.data.meshes.new_from_object(
		obj.evaluated_get(depsgraph),
		preserve_all_data_layers=True,
		depsgraph=depsgraph,
	)
	mesh.transform(obj.matrix_world)
	mesh.name = name
	copy = bpy.data.objects.new(_free_name(name), mesh)
	_link_temp(copy)
	return copy


def _copy_object(obj: bpy.types.Object, name: str) -> bpy.types.Object:
	"""Copy a non-mesh object at its world transform, without its parent."""
	copy = obj.copy()
	if obj.data is not None:
		copy.data = obj.data.copy()
	copy.parent = None
	copy.matrix_world = obj.matrix_world
	copy.name = _free_name(name)
	_link_temp(copy)
	return copy


def _prepare_vertex_colors(obj: bpy.types.Object) -> None:
	"""Make the painted channels explicit: unpainted vertices become black, not white."""
	attrs = obj.data.color_attributes
	attr = attrs.active_color if attrs.active_color is not None else (attrs[0] if len(attrs) else None)
	if attr is None:
		attr = attrs.new(name="Attribute", type="BYTE_COLOR", domain="CORNER")
		for element in attr.data:
			element.color = (0.0, 0.0, 0.0, 1.0)
	else:
		if attr.name != "Attribute":
			attr.name = "Attribute"
		for element in attr.data:
			color = element.color
			if color[0] > 0.999 and color[1] > 0.999 and color[2] > 0.999:
				element.color = (0.0, 0.0, 0.0, color[3])
	for extra in list(attrs):
		if extra != attr:
			attrs.remove(extra)
	attrs.active_color_index = attrs.find(attr.name) if attrs.find(attr.name) >= 0 else 0
	attrs.render_color_index = attrs.active_color_index


def _join_meshes(objs: list, name: str) -> bpy.types.Object:
	if not objs:
		raise RuntimeError("No meshes to join for %s" % name)
	parts = [_evaluated_mesh(obj, "%s_%d" % (name, i)) for i, obj in enumerate(objs)]
	for part in parts:
		_prepare_vertex_colors(part)
	target = parts[0]
	with bpy.context.temp_override(active_object=target, selected_editable_objects=parts, object=target):
		bpy.ops.object.join()
	for joined in parts[1:]:
		if joined in _temp_objects:
			_temp_objects.remove(joined)
	target.name = _free_name(name)
	target.data.name = name
	bm = bmesh.new()
	bm.from_mesh(target.data)
	bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0005)
	bm.to_mesh(target.data)
	bm.free()
	attrs = target.data.color_attributes
	if attrs and attrs.find("Attribute") >= 0:
		attrs.active_color_index = attrs.find("Attribute")
		attrs.render_color_index = attrs.active_color_index
	return target


def _center_mesh(obj: bpy.types.Object) -> Vector:
	"""Move the baked mesh so its XZ bounds centre on the origin and its floor sits at Y=0."""
	low = Vector((float("inf"), float("inf"), float("inf")))
	high = Vector((float("-inf"), float("-inf"), float("-inf")))
	for corner in obj.bound_box:
		point = obj.matrix_world @ Vector(corner)
		for axis in range(3):
			low[axis] = min(low[axis], point[axis])
			high[axis] = max(high[axis], point[axis])
	offset = Vector((-(low.x + high.x) * 0.5, -(low.y + high.y) * 0.5, -low.z))
	obj.data.transform(Matrix.Translation(offset))
	obj.data.update()
	print("center offset:", tuple(round(v, 6) for v in offset))
	return offset


def _root_object(name: str) -> bpy.types.Object:
	root = bpy.data.objects.new(_free_name(name), None)
	_link_temp(root)
	root.empty_display_type = "PLAIN_AXES"
	root.empty_display_size = 0.5
	root["dz_export_root"] = True
	return root


def _select_tree(root: bpy.types.Object | None, extra: list | None = None) -> list:
	objects = ([root] + list(root.children_recursive) if root is not None else []) + (extra or [])
	bpy.ops.object.select_all(action="DESELECT")
	for obj in objects:
		obj.hide_set(False)
		obj.select_set(True)
	if objects:
		bpy.context.view_layer.objects.active = objects[0]
	return objects


def _cleanup() -> None:
	for obj in _temp_objects:
		data = obj.data
		bpy.data.objects.remove(obj)
		if data is not None and data.users == 0:
			if isinstance(data, bpy.types.Mesh):
				bpy.data.meshes.remove(data)
			elif isinstance(data, bpy.types.Camera):
				bpy.data.cameras.remove(data)
			elif isinstance(data, bpy.types.Light):
				bpy.data.lights.remove(data)
			elif isinstance(data, bpy.types.Armature):
				bpy.data.armatures.remove(data)
	_temp_objects.clear()
	for obj, name in reversed(_renamed):
		obj.name = name
	_renamed.clear()
	for material in _temp_materials:
		if material.users == 0:
			bpy.data.materials.remove(material)
	_temp_materials.clear()
	for material, name in reversed(_renamed_materials):
		material.name = name
	_renamed_materials.clear()
	col = bpy.data.collections.get(TEMP_COLLECTION)
	if col is not None:
		bpy.data.collections.remove(col)


def _image_records() -> list:
	records = []
	for image in bpy.data.images:
		if image.name == "Render Result" or image.type == "RENDER_RESULT":
			continue
		filepath = bpy.path.abspath(image.filepath) if image.filepath else ""
		exists = bool(filepath) and os.path.isfile(filepath)
		records.append({
			"name": image.name,
			"stem": os.path.splitext(image.name)[0],
			"filepath": filepath if exists else "",
			"basename": os.path.basename(filepath) if exists else "",
		})
	return records


def _source_relative_path(source: Path) -> Path:
	"""Everything below the lowest "assets" folder in the source path."""
	parts = source.parts
	index = -1
	for i, part in enumerate(parts):
		if part == ASSET_ANCHOR:
			index = i
	if index >= 0 and index + 1 < len(parts):
		return Path(*parts[index + 1:])
	return Path(source.parent.name) / source.name


def _texture_destination(record: dict | None, exported_name: str, texture_root: Path) -> Path:
	if record is not None and record["filepath"]:
		source = Path(record["filepath"])
		relative = _source_relative_path(source)
		exact = texture_root / relative
		if exact.is_file():
			return relative
		for match in texture_root.rglob(source.name):
			if match.is_file():
				return match.relative_to(texture_root)
		return relative
	return Path("generated") / exported_name


def _record_for_image(image: dict, records: list, exported_name: str) -> dict | None:
	name = str(image.get("name", ""))
	candidates = [r for r in records if r["stem"] == name or r["name"] == name]
	if len(candidates) == 1:
		return candidates[0]
	candidates = [r for r in records if r["basename"] == exported_name]
	if len(candidates) == 1:
		return candidates[0]
	return candidates[0] if candidates else None


def _material_images(materials: list) -> list:
	"""Every image referenced by these materials, including groups."""
	found: dict = {}

	def walk(tree) -> None:
		if tree is None:
			return
		for node in tree.nodes:
			if node.type == "TEX_IMAGE" and node.image is not None:
				found[node.image.name] = node.image
			elif node.type == "GROUP" and node.node_tree is not None:
				walk(node.node_tree)

	for material in materials:
		if material is not None and material.use_nodes:
			walk(material.node_tree)
	return list(found.values())


def _copy_extra_textures(images: list, texture_root: Path, records: list, written: list) -> list:
	"""Copy material textures that glTF does not reference (shader-only maps) to the shared tree."""
	by_name = {record["name"]: record for record in records}
	done = {path.resolve() for path in written}
	extra = []
	for image in images:
		record = by_name.get(image.name)
		if record is None or not record["filepath"]:
			continue
		source = Path(record["filepath"])
		relative = _texture_destination(record, source.name, texture_root)
		destination = (texture_root / relative).resolve()
		if destination in done:
			continue
		destination.parent.mkdir(parents=True, exist_ok=True)
		if destination != source and (not destination.is_file() or destination.read_bytes() != source.read_bytes()):
			shutil.copyfile(source, destination)
		done.add(destination)
		extra.append(destination)
	return extra


def _reuse_textures(gltf_path: Path, texture_root: Path, records: list) -> list:
	"""Move exported textures into the shared texture tree and make URIs relative."""
	data = json.loads(gltf_path.read_text(encoding="utf-8"))
	written = []
	for image in data.get("images", []):
		uri = image.get("uri")
		if not uri or uri.startswith("data:"):
			continue
		exported = (gltf_path.parent / uri).resolve()
		if not exported.is_file():
			continue
		exported_name = exported.name
		record = _record_for_image(image, records, exported_name)
		relative = _texture_destination(record, exported_name, texture_root)
		destination = (texture_root / relative).resolve()
		destination.parent.mkdir(parents=True, exist_ok=True)
		if not destination.is_file() or destination.read_bytes() != exported.read_bytes():
			shutil.copyfile(exported, destination)
		image["uri"] = Path(os.path.relpath(destination, gltf_path.parent)).as_posix()
		written.append(destination)
	gltf_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
	return written


def _parse_args() -> argparse.Namespace:
	argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
	parser = argparse.ArgumentParser(description="Export a Blender collection as glTF Separate.")
	parser.add_argument("--collection", default=None)
	parser.add_argument("--output", default=None)
	parser.add_argument("--texture-root", default=None)
	parser.add_argument("--center", action=argparse.BooleanOptionalAction, default=None)
	parser.add_argument("--animations", action="store_true")
	parser.add_argument("--root-name", default=None)
	return parser.parse_args(argv)


def export_scene(
	collection: str | None = None,
	output: str | Path | None = None,
	texture_root: str | Path | None = None,
	center: bool | None = None,
	root_name: str | None = None,
	animations: bool = False,
) -> str:
	args = _parse_args()
	collection = collection or args.collection or DEFAULT_COLLECTION
	output = output or args.output
	texture_root = texture_root or args.texture_root
	center = center if center is not None else args.center
	root_name = root_name or args.root_name
	animations = animations or args.animations

	if bpy.context.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")

	col = bpy.data.collections.get(collection)
	if col is None:
		raise RuntimeError("Collection %s missing in %s" % (collection, bpy.data.filepath))

	if output is None:
		output = col.get("dz_export_path")
	if output is None:
		# No project layout to fall back on: export next to the .blend.
		stem = Path(bpy.data.filepath).stem or DEFAULT_COLLECTION
		output = _blend_dir() / (stem + ".gltf")
	out_path = _resolve(output)
	if out_path.suffix.lower() != ".gltf":
		out_path = out_path.with_suffix(".gltf")

	if texture_root is None:
		texture_root = col.get("dz_texture_root")
	tex_root = _resolve(texture_root) if texture_root else out_path.parent
	tex_root.mkdir(parents=True, exist_ok=True)

	if center is None:
		center = bool(col.get("dz_center", False))
	if root_name is None:
		root_name = str(col.get("dz_export_root", out_path.stem))

	objects = [obj for obj in col.all_objects if _exported(obj)]
	for obj in objects:
		if obj.type == "LIGHT" and obj.data.type not in SUPPORTED_LIGHT_TYPES:
			print("skipping unsupported light:", obj.name, obj.data.type)
	meshes = [obj for obj in objects if obj.type == "MESH"]
	others = [obj for obj in objects if obj.type != "MESH" and _exportable_other(obj)]
	if not meshes and not others:
		raise RuntimeError("Collection %s has nothing to export" % collection)

	records = _image_records()
	out_path.parent.mkdir(parents=True, exist_ok=True)
	texture_dir = Path(tempfile.mkdtemp(prefix=".dz_export_tex_", dir=out_path.parent))
	try:
		root = _root_object(root_name)
		static = None
		offset = Vector((0.0, 0.0, 0.0))
		if meshes:
			static = _join_meshes(meshes, "Static")
			static["dz_static"] = True
			if center:
				offset = _center_mesh(static)
			static.parent = root
			static.matrix_parent_inverse = Matrix.Identity(4)
		extras = [_copy_object(obj, obj.name) for obj in others]
		if offset.length > 0.0:
			for extra in extras:
				extra.matrix_world = Matrix.Translation(offset) @ extra.matrix_world
		material_cache: dict = {}
		if static is not None:
			for slot in static.material_slots:
				if slot.material is not None:
					slot.material = _export_material(slot.material, material_cache)
		materials = [slot.material for slot in (static.material_slots if static is not None else [])]
		selected = _select_tree(root, extras)
		bpy.ops.export_scene.gltf(
			filepath=str(out_path),
			export_format="GLTF_SEPARATE",
			use_selection=True,
			export_extras=True,
			export_apply=True,
			export_yup=True,
			export_cameras=True,
			export_lights=True,
			export_animations=animations,
			export_texcoords=True,
			export_normals=True,
			export_tangents=True,
			export_vertex_color="ACTIVE",
			export_all_vertex_colors=False,
			export_image_format="AUTO",
			export_texture_dir=str(texture_dir),
			export_materials="EXPORT",
		)
		written = _reuse_textures(out_path, tex_root, records)
		written += _copy_extra_textures(_material_images(materials), tex_root, records, written)
		print("exported", out_path)
		print("textures:", len(written))
		for path in written:
			print("  ", path)
		print("objects:", [obj.name for obj in selected])
		return str(out_path)
	finally:
		shutil.rmtree(texture_dir, ignore_errors=True)
		_cleanup()


def export_objects(
	objects: list,
	output: str | Path,
	texture_root: str | Path | None = None,
	animations: bool = False,
	vertex_color: str = "ACTIVE",
	export_all_vertex_colors: bool = False,
	cameras: bool = False,
	lights: bool = False,
) -> str:
	"""Export exactly these objects as GLTF_SEPARATE, reusing textures."""
	if bpy.context.mode != "OBJECT":
		bpy.ops.object.mode_set(mode="OBJECT")
	if not objects:
		raise RuntimeError("No objects to export")
	out_path = _resolve(output)
	if out_path.suffix.lower() != ".gltf":
		out_path = out_path.with_suffix(".gltf")
	tex_root = _resolve(texture_root) if texture_root else out_path.parent
	tex_root.mkdir(parents=True, exist_ok=True)
	records = _image_records()
	out_path.parent.mkdir(parents=True, exist_ok=True)
	texture_dir = Path(tempfile.mkdtemp(prefix=".dz_export_tex_", dir=out_path.parent))
	try:
		material_cache: dict = {}
		materials = []
		for obj in objects:
			if obj.type != "MESH":
				continue
			for slot in obj.material_slots:
				if slot.material is not None:
					slot.material = _export_material(slot.material, material_cache)
			materials.extend(slot.material for slot in obj.material_slots)
		selected = _select_tree(None, objects)
		bpy.ops.export_scene.gltf(
			filepath=str(out_path),
			export_format="GLTF_SEPARATE",
			use_selection=True,
			export_extras=True,
			export_apply=True,
			export_yup=True,
			export_cameras=cameras,
			export_lights=lights,
			export_animations=animations,
			export_texcoords=True,
			export_normals=True,
			export_tangents=True,
			export_vertex_color=vertex_color,
			export_all_vertex_colors=export_all_vertex_colors,
			export_image_format="AUTO",
			export_texture_dir=str(texture_dir),
			export_materials="EXPORT",
		)
		written = _reuse_textures(out_path, tex_root, records)
		written += _copy_extra_textures(_material_images(materials), tex_root, records, written)
		print("exported", out_path)
		print("textures:", len(written))
		for path in written:
			print("  ", path)
		print("objects:", [obj.name for obj in selected])
		return str(out_path)
	finally:
		shutil.rmtree(texture_dir, ignore_errors=True)
		_cleanup()


def main() -> str:
	return export_scene()


if __name__ == "__main__":
	main()

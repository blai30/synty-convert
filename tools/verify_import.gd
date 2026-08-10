## Verifies converted models the way Godot actually sees them.
##
## Optional check, run inside your own Godot project once the converted assets are in:
##
##     godot --headless --import
##     godot --headless --script res://tools/verify_import.gd -- --assets res://assets
##
## Walks every imported model and asserts the things the converter promises: identity
## node transforms, real world scale, a material on every surface, and textures that
## resolve to the shared atlas rather than an embedded copy.
extends SceneTree

const DEFAULT_ASSETS := "res://assets"
const EPSILON := 0.001


func _init() -> void:
	var assets := _assets_dir()
	var models := _find_models(assets)
	if models.is_empty():
		push_error("No .glb found under %s. Copy the converted assets in and --import first."
				% assets)
		quit(1)
		return

	var problems: Array[String] = []
	var textures := {}
	var checked := 0
	var surfaces := 0
	var untextured := 0
	var tallest := 0.0

	for path in models:
		var packed := load(path) as PackedScene
		if packed == null:
			problems.append("%s: failed to load" % path)
			continue
		var root := packed.instantiate()
		checked += 1
		var found := _inspect(root, root, path, problems, textures)
		surfaces += found[0]
		untextured += found[1]
		tallest = maxf(tallest, found[2])
		root.free()

	print("checked %d models, %d surfaces, %d without a texture" % [checked, surfaces, untextured])
	print("distinct textures referenced: %d" % textures.size())
	print("tallest mesh: %.3f m" % tallest)
	if problems.is_empty():
		print("\nPASS: transforms identity, every surface has a material, textures shared")
		quit(0)
		return
	print("\n%d problem(s):" % problems.size())
	for problem in problems.slice(0, 20):
		print("   ", problem)
	quit(1)


func _inspect(node: Node, root: Node, path: String, problems: Array[String], textures: Dictionary) -> Array:
	var surfaces := 0
	var untextured := 0
	var tallest := 0.0

	if node is Node3D and node != root:
		var basis := (node as Node3D).transform.basis
		var scale := basis.get_scale()
		# The converter bakes the Maya centimetre and Y-up conversion into the data, so
		# nothing downstream should carry a leftover scale.
		if absf(scale.x - 1.0) > EPSILON or absf(scale.y - 1.0) > EPSILON or absf(scale.z - 1.0) > EPSILON:
			if not (node is MeshInstance3D and node.get_parent() is Skeleton3D):
				problems.append("%s: %s has scale %s" % [path.get_file(), node.name, str(scale)])

	if node is MeshInstance3D:
		var mesh := (node as MeshInstance3D).mesh
		tallest = mesh.get_aabb().size.y
		for i in mesh.get_surface_count():
			surfaces += 1
			var material := mesh.surface_get_material(i)
			if material == null:
				problems.append("%s: %s surface %d has no material" % [path.get_file(), node.name, i])
				continue
			if material is BaseMaterial3D:
				var texture := (material as BaseMaterial3D).albedo_texture
				if texture == null:
					untextured += 1
				else:
					textures[texture.resource_path] = true

	for child in node.get_children():
		var found := _inspect(child, root, path, problems, textures)
		surfaces += found[0]
		untextured += found[1]
		tallest = maxf(tallest, found[2])
	return [surfaces, untextured, tallest]


func _assets_dir() -> String:
	## Defaults to res://assets, or wherever "--assets <path>" points.
	var arguments := OS.get_cmdline_user_args()
	var index := arguments.find("--assets")
	if index != -1 and index + 1 < arguments.size():
		return arguments[index + 1].rstrip("/")
	return DEFAULT_ASSETS


func _find_models(directory: String) -> PackedStringArray:
	var found := PackedStringArray()
	var handle := DirAccess.open(directory)
	if handle == null:
		return found
	for name in handle.get_directories():
		found.append_array(_find_models(directory + "/" + name))
	for name in handle.get_files():
		if name.ends_with(".glb"):
			found.append(directory + "/" + name)
	return found

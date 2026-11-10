# Blender indie game-ready scenes

Game-ready Blender scenes plus scripts that export them to Godot as **glTF Separate** (`.gltf` + `.bin` + a shared texture tree).

## Running the exports

Run Blender from the folder that holds the `.blend`, headless:

```sh
cd projects/main_menu_background_scene
blender -b main_menu_background_scene.blend --python ../../scripts/export_main_menu_background.py
blender -b main_menu_background_scene.blend --python ../../scripts/export_main_menu_door.py
```

Generic exporter, for any scene:

```sh
blender -b scene.blend --python scripts/export_scene.py -- \
    --collection scene \
    --output ../../output/models/scene.gltf \
    --texture-root ../../output/assets/textures \
    --root-name SceneRoot --center
```

## Asset credits

| Asset | Source | Licence |
| --- | --- | --- |
| PSX Low-poly Fire Extinguisher (`Sketchfab_model` → `Object_2`, hidden reference prop in the `scene` collection) | [korkskrew2000 — PSX Low-poly Fire Extinguisher](https://sketchfab.com/3d-models/psx-low-poly-fire-extinguisher-d20fefb12d5f45e382f5963ef64e7a44) | CC BY-SA 4.0 |

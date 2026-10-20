# Export Bungalov.blend to web/bungalov.glb for the browser walkthrough.
# Run headless (never saves the .blend):
#   /Applications/Blender.app/Contents/MacOS/Blender -b Bungalov.blend --python web/export_web.py
import bpy, bmesh, os, json, mathutils

OUT = os.environ.get("WEB_OUT") or os.path.join(os.path.dirname(bpy.data.filepath), "web")
MAX_TEX = 1024
SKIP_COLLS = {"_old furniture", "Lights"}

def visible(o):
    if o.hide_render:
        return False
    return all(not c.hide_render and c.name not in SKIP_COLLS for c in o.users_collection)

# ---------- 1. materials: object/generated box mapping -> UV, procedural colour -> baked texture ----------
BAKE_SIZE = 2.0   # metres of surface covered by one baked tile
BAKE_RES = 1024
sel = [o for o in bpy.context.scene.objects if visible(o) and o.type in {'MESH', 'CURVE', 'CAMERA'}]
used = {s.material for o in sel if o.type == 'MESH' for s in o.material_slots if s.material and s.material.node_tree}

def principled(m):
    return next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)

def needs_bake(m):
    p = principled(m)
    return p and p.inputs['Base Color'].links and p.inputs['Base Color'].links[0].from_node.type != 'TEX_IMAGE'

def bake_base_colors(mats):
    """Bake each procedural Base Color onto a BAKE_SIZE x BAKE_SIZE plane in object space."""
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 1
    me = bpy.data.meshes.new('_bake')
    S = BAKE_SIZE
    me.from_pydata([(0, 0, 0), (S, 0, 0), (S, S, 0), (0, S, 0)], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new()
    for i, c in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
        uv.data[i].uv = c
    ob = bpy.data.objects.new('_bake', me)
    scene.collection.objects.link(ob)
    for o in scene.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    baked = {}
    for m in mats:
        me.materials.clear()
        me.materials.append(m)
        img = bpy.data.images.new(m.name + '_bake', BAKE_RES, BAKE_RES)
        node = m.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = img
        m.node_tree.nodes.active = node
        bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, margin=0)
        img.pack()
        baked[m] = node
    bpy.data.objects.remove(ob)
    return baked

baked = bake_base_colors([m for m in used if needs_bake(m)])
print("BAKED", sorted(m.name for m in baked))

for m in used:
    nt = m.node_tree
    for n in list(nt.nodes):
        if n.type == 'TEX_COORD':
            for out in ('Object', 'Generated'):
                for l in list(n.outputs[out].links):
                    nt.links.new(n.outputs['UV'], l.to_socket)
        if n.type == 'TEX_IMAGE':
            n.projection = 'FLAT'
    p = principled(m)
    if not p:
        continue
    if m in baked:
        node = baked[m]
        tc = nt.nodes.new('ShaderNodeTexCoord')
        mp = nt.nodes.new('ShaderNodeMapping')
        mp.inputs['Scale'].default_value = (1 / BAKE_SIZE, 1 / BAKE_SIZE, 1)
        nt.links.new(tc.outputs['UV'], mp.inputs['Vector'])
        nt.links.new(mp.outputs['Vector'], node.inputs['Vector'])
        nt.links.new(node.outputs['Color'], p.inputs['Base Color'])
    for name in ('Roughness', 'Metallic'):
        s = p.inputs[name]
        if s.links and s.links[0].from_node.type not in ('TEX_IMAGE', 'SEPARATE_COLOR'):
            nt.links.remove(s.links[0])

# ---------- 2. box-projected UVs from object coordinates (matches Blender Box/Object mapping) ----------
done = set()
for o in sel:
    if o.type != 'MESH' or o.data in done:
        continue
    done.add(o.data)
    me = o.data
    bm = bmesh.new(); bm.from_mesh(me)
    uv = bm.loops.layers.uv.verify()
    for f in bm.faces:
        nx, ny, nz = (abs(c) for c in f.normal)
        for lp in f.loops:
            x, y, z = lp.vert.co
            if nz >= nx and nz >= ny:
                lp[uv].uv = (x, y)
            elif nx >= ny:
                lp[uv].uv = (y, z)
            else:
                lp[uv].uv = (x, z)
    bm.to_mesh(me); bm.free()
    while len(me.uv_layers) > 1:
        me.uv_layers.remove(me.uv_layers[0])

# ---------- 3. tag collection for the viewer, downscale textures ----------
for o in sel:
    o["coll"] = o.users_collection[0].name
for img in bpy.data.images:
    if img.size[0] > MAX_TEX and img.type == 'IMAGE':
        lim = 2 * MAX_TEX if "4k" in img.name else MAX_TEX
        f = lim / max(img.size)
        if f < 1:
            img.scale(int(img.size[0] * f), int(img.size[1] * f))

# environment panorama for the sky
w = bpy.context.scene.world
env = next((n.image for n in w.node_tree.nodes if n.type == 'TEX_ENVIRONMENT' and n.image), None) if w and w.node_tree else None
if env:
    env.scale(2048, 1024)
    st = bpy.context.scene.render.image_settings
    st.file_format = 'JPEG'; st.color_mode = 'RGB'; st.quality = 85
    env.save_render(os.path.join(OUT, "sky.jpg"))

# ---------- 4. export ----------
bpy.ops.object.select_all(action='DESELECT')
for o in sel:
    o.select_set(True)
bpy.ops.export_scene.gltf(
    filepath=os.path.join(OUT, "bungalov.glb"), export_format='GLB',
    use_selection=True, export_apply=True, export_cameras=True, export_lights=False,
    export_extras=True, export_image_format='JPEG', export_jpeg_quality=80,
    export_animations=False, export_yup=True,
)
print("EXPORTED", len(sel))

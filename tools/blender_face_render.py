# tools/blender_face_render.py —— 用 Blender 4.2 无头渲染她的脸部特写（可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 用法：blender -b --factory-startup -P tools/blender_face_render.py -- <glb> <out.png> [frame_h]
# 口径：灯用 Sun（面积灯在这个 1 单位高的模型上会直接打爆），色彩用 Standard，取景对准头顶下 9%。
import bpy, math, os, sys
from mathutils import Vector

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
GLB = argv[0] if argv else r'C:\Users\ADMIN\Desktop\GBT小土豆V9\state\tripo\out2\tripo-out\state-tripo-ref-full-5215f8bc\model.glb'
OUT = argv[1] if len(argv) > 1 else r'C:\Users\ADMIN\Desktop\GBT小土豆V9\state\tripo\render\face_hires.png'
FRAME = float(argv[2]) if len(argv) > 2 else 0.10

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
o = [x for x in bpy.data.objects if x.type == 'MESH'][0]
vs = [o.matrix_world @ v.co for v in o.data.vertices]
zmin = min(v.z for v in vs); zmax = max(v.z for v in vs); H = zmax - zmin
# 脸的位置：头顶下 9%（不是顶点云重心 —— 头发会把重心带偏，踩过）
cut = zmax - H * 0.09
band = [v for v in vs if v.z >= cut]
aim = Vector((sum(v.x for v in band)/len(band), sum(v.y for v in band)/len(band), cut))
print('顶点 %d · 身高 %.3f · 瞄准 %.3f,%.3f,%.3f' % (len(vs), H, aim.x, aim.y, aim.z))

sc = bpy.context.scene
sc.render.engine = 'BLENDER_EEVEE_NEXT'
sc.eevee.taa_render_samples = 128
sc.render.resolution_x = 2048; sc.render.resolution_y = 2048
sc.view_settings.view_transform = 'Standard'      # AgX 会把肤色压灰；Standard 更接近渲染件原貌
sc.view_settings.exposure = 0.0
w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True
w.node_tree.nodes['Background'].inputs[0].default_value = (0.04, 0.05, 0.07, 1)
w.node_tree.nodes['Background'].inputs[1].default_value = 0.35

def sun(name, rot, energy):
    d = bpy.data.lights.new(name, 'SUN'); d.energy = energy; d.angle = math.radians(15)
    ob = bpy.data.objects.new(name, d); sc.collection.objects.link(ob)
    ob.rotation_euler = rot; return ob
sun('key',  (math.radians(55), 0, math.radians(35)), 3.2)
sun('fill', (math.radians(70), 0, math.radians(-80)), 1.1)
sun('rim',  (math.radians(115), 0, math.radians(200)), 2.4)

aimo = bpy.data.objects.new('aim', None); sc.collection.objects.link(aimo); aimo.location = aim
cd = bpy.data.cameras.new('c'); cd.lens = 150
cam = bpy.data.objects.new('c', cd); sc.collection.objects.link(cam); sc.camera = cam
ct = cam.constraints.new('TRACK_TO'); ct.target = aimo
dist = H * FRAME * 3.2
for tag, sy, zz in (('f', 1.0, 0.02), ('q', 1.0, 0.03)):
    cam.location = (aim.x + (0.06*H if tag == 'q' else 0.0), aim.y + sy*dist, aim.z + H*zz)
    sc.render.filepath = OUT if tag == 'f' else OUT.replace('.png', '_q.png')
    bpy.ops.render.render(write_still=True)
    print('渲染:', sc.render.filepath)
print('DONE')

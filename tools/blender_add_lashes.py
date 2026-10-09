# tools/blender_add_lashes.py —— 给现有模型加睫毛几何（主人选 A）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 为什么是"加几何"而不是调材质：该模型只有 1 个材质、alphaMode=OPAQUE（没有睫毛几何/alpha 卡片），
#   只有**真加几何**才能让睫毛可见。
# 定位演进（都留痕）：
#   第一版按"朝脸侧最前顶点"估眼位 ⇒ **红标记悬在头发上**（这姿势头发盖在脸前，前向顶点全是头发）。
#   第二版（本文件）改用**颜色贴图采样皮肤**：按 UV 取色，R>G>B 且够亮 = 皮肤；在眼部高度带里
#   只留皮肤顶点，再按中线分左右，取上缘当眼睑。这是"看图取色"而不是"猜几何"。
import bpy, math, os, sys
import numpy as np
from mathutils import Vector

GLB = r'C:\Users\ADMIN\Desktop\GBT小土豆V9\state\tripo\out2\tripo-out\state-tripo-ref-full-5215f8bc\model.glb'
OUT = r'C:\Users\ADMIN\Desktop\GBT小土豆V9\state\tripo\render'
MARK = (sys.argv[sys.argv.index('--') + 1] == 'mark') if '--' in sys.argv else True

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=GLB)
head = [o for o in bpy.data.objects if o.type == 'MESH'][0]
me = head.data
vs = [head.matrix_world @ v.co for v in me.vertices]
zmin = min(v.z for v in vs); zmax = max(v.z for v in vs); H = zmax - zmin
ymax = max(v.y for v in vs)
print('身高 %.3f' % H)

# 颜色贴图 → numpy
img = next((i for i in bpy.data.images if i.name.lower().startswith('color')), None)
skin = set()
if img is not None and img.size[0]:
    w, h = img.size
    buf = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    tex = buf.reshape(h, w, 4)
    uvl = me.uv_layers.active.data if me.uv_layers.active else None
    if uvl is not None:
        v_uv = {}
        for loop in me.loops:
            v_uv.setdefault(loop.vertex_index, (uvl[loop.index].uv[0], uvl[loop.index].uv[1]))
        for vi, uv in v_uv.items():
            x = int(min(max(uv[0], 0.0), 0.999) * (w - 1))
            y = int(min(max(uv[1], 0.0), 0.999) * (h - 1))
            r, g, b = tex[y, x, 0], tex[y, x, 1], tex[y, x, 2]
            if r > 0.22 and r > g * 1.04 and g > b * 0.92 and (r - b) > 0.03:
                skin.add(vi)
print('皮肤顶点（按贴图取色）: %d / %d' % (len(skin), len(me.vertices)))

# 第二版把眼带放太高（压到刘海/眉弓，真眼在更低处）⇒ 下移：取头顶下 8.5%~15.5% 那一段皮肤
band = [me.vertices[i].co.copy() for i in skin
        if zmax - 0.155 * H <= (head.matrix_world @ me.vertices[i].co).z <= zmax - 0.085 * H]
band = [head.matrix_world @ p for p in band]
print('眼部高度带里的皮肤顶点: %d' % len(band))
if band:
    cx = sum(v.x for v in band) / len(band)
    fy = max(v.y for v in band)
    left = [v for v in band if v.x < cx]; right = [v for v in band if v.x >= cx]
    def eye_of(group):
        if not group:
            return None
        # 眼睑线 = 该群**最靠前**（y 最大）的那批皮肤顶点 —— 那才是脸的正面/眼睑，
        # 而不是"最高的那批"（最高的是额头/刘海，踩过）。
        gy = sorted(v.y for v in group)
        y90 = gy[int(len(gy) * 0.9)]
        frontmost = [v for v in group if v.y >= y90 - 0.004 * H] or group
        return Vector((sum(v.x for v in frontmost) / len(frontmost),
                       max(v.y for v in frontmost),
                       sum(v.z for v in frontmost) / len(frontmost)))
    eL, eR = eye_of(left), eye_of(right)
    print('眼位估计: L=%s R=%s' % (tuple(round(x, 3) for x in eL) if eL else None,
                                  tuple(round(x, 3) for x in eR) if eR else None))

    # 校准第三版显示：位置基本对但**偏高一点、太长太密** ⇒ 下移 0.008H、缩短、稀疏
    def lash_fan(side, eye, n=12, length=0.011):
        eye = Vector((eye.x, eye.y, eye.z - 0.008 * H))
        mat = bpy.data.materials.get('lash_mat')
        if mat is None:
            mat = bpy.data.materials.new('lash_mat'); mat.use_nodes = True
            b = mat.node_tree.nodes['Principled BSDF']
            b.inputs['Base Color'].default_value = (0.9, 0.05, 0.05, 1) if MARK else (0.04, 0.03, 0.03, 1)
            b.inputs['Roughness'].default_value = 0.45
        verts_, faces = [], []
        for i in range(n):
            t = (i / (n - 1)) - 0.5
            px = eye.x + t * 0.022 * H
            pz = eye.z + math.cos(t * 3.14) * 0.005 * H
            w = 0.0007 * H
            tipz = pz + length * (0.5 + 0.5 * math.cos(t * 3.14))
            tipy = eye.y + length * 0.55      # 睫毛往外翘（沿脸法线方向），拉长一点才看得见
            base = len(verts_)
            verts_ += [Vector((px - w, eye.y, pz)), Vector((px + w, eye.y, pz)),
                       Vector((px - w * 0.3, tipy, tipz)), Vector((px + w * 0.3, tipy, tipz))]
            faces.append((base, base + 1, base + 3, base + 2))
        m2 = bpy.data.meshes.new('lash_%d' % side)
        m2.from_pydata([tuple(v) for v in verts_], [], faces)
        m2.update()
        ob = bpy.data.objects.new('lash_%d' % side, m2)
        ob.data.materials.append(mat)
        bpy.context.scene.collection.objects.link(ob)

    if eL: lash_fan(-1, eL)
    if eR: lash_fan(1, eR)

sc = bpy.context.scene
sc.render.engine = 'BLENDER_EEVEE_NEXT'; sc.eevee.taa_render_samples = 64
sc.render.resolution_x = 1024; sc.render.resolution_y = 1024
sc.view_settings.view_transform = 'Standard'
w = bpy.data.worlds.new('w'); sc.world = w; w.use_nodes = True
w.node_tree.nodes['Background'].inputs[0].default_value = (0.05, 0.06, 0.08, 1)
w.node_tree.nodes['Background'].inputs[1].default_value = 0.4
def sun(n, rot, e):
    d = bpy.data.lights.new(n, 'SUN'); d.energy = e; d.angle = math.radians(15)
    ob = bpy.data.objects.new(n, d); sc.collection.objects.link(ob); ob.rotation_euler = rot; return ob
sun('k', (math.radians(55), 0, math.radians(35)), 3.0)
sun('f', (math.radians(70), 0, math.radians(-80)), 1.2)
sun('r', (math.radians(115), 0, math.radians(200)), 2.2)
aim = bpy.data.objects.new('aim', None); sc.collection.objects.link(aim)
aim.location = (cx if band else 0.0, 0.0, zmax - 0.095 * H)
cd = bpy.data.cameras.new('c'); cd.lens = 135
cam = bpy.data.objects.new('c', cd); sc.collection.objects.link(cam); sc.camera = cam
ct = cam.constraints.new('TRACK_TO'); ct.target = aim
cam.location = (aim.location.x + 0.01 * H, ymax + H * 0.28, zmax - 0.070 * H)
sc.render.filepath = os.path.join(OUT, '_lash_mark2.png' if MARK else 'face_lashes.png')
bpy.ops.render.render(write_still=True)
print('渲染:', sc.render.filepath)

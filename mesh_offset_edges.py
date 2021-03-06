# ***** BEGIN GPL LICENSE BLOCK *****
#
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ***** END GPL LICENCE BLOCK *****

# <pep8 compliant>

bl_info = {
    "name": "Offset Edges",
    "author": "Hidesato Ikeya",
    "version": (0, 2, 1),
    "blender": (2, 70, 0),
    "location": "VIEW3D > Edge menu(CTRL-E) > Offset Edges",
    "description": "Offset Edges",
    "warning": "",
    "wiki_url": "http://wiki.blender.org/index.php/Extensions:2.6/Py/Scripts/Modeling/offset_edges",
    "tracker_url": "",
    "category": "Mesh"}

import math
from math import sin, cos, pi
import bpy
import bmesh
from mathutils import Vector
from time import perf_counter

X_UP = Vector((1.0, .0, .0))
Y_UP = Vector((.0, 1.0, .0))
Z_UP = Vector((.0, .0, 1.0))
ZERO_VEC = Vector((.0, .0, .0))
ANGLE_90 = pi / 2
ANGLE_180 = pi
ANGLE_360 = 2 * pi


def calc_normal_from_verts(verts, fallback=Z_UP):
    # Calculate normal from verts using Newell's method.
    normal = ZERO_VEC.copy()

    verts_2 = verts[1:]
    if verts[0] is not verts[-1]:
        # Half loop.
        verts_2.append(verts[0])
    for v1, v2 in zip(verts, verts_2):
        v1co, v2co = v1.co, v2.co
        normal.x += (v1co.y - v2co.y) * (v1co.z + v2co.z)
        normal.y += (v1co.z - v2co.z) * (v1co.x + v2co.x)
        normal.z += (v1co.x - v2co.x) * (v1co.y + v2co.y)

    normal.normalize()
    if normal == ZERO_VEC:
        normal = fallback

    return normal

def get_corner_type(vec_up, vec_right2d, vec_left2d, threshold=1.0e-4):
    # vec_right2d and vec_left2d should be perpendicular to vec_up.
    # All vectors in parameters should have been normalized.
    if vec_right2d == vec_left2d == ZERO_VEC:
        return 'FOLDING'
    elif vec_right2d == ZERO_VEC or vec_left2d == ZERO_VEC:
        return 'STRAIGHT'

    angle = vec_right2d.angle(vec_left2d)
    if angle < threshold:
        return 'FOLDING'
    elif angle > ANGLE_180 - threshold:
        return 'STRAIGHT'
    elif vec_right2d.cross(vec_left2d).dot(vec_up) > threshold:
        return 'CONVEX'
    else:
        return 'CONCAVE'

def calc_tangent(vec_up, vec_right, vec_left, threshold=1.0e-4):
    vec_right2d = vec_right- vec_right.project(vec_up)
    vec_right2d.normalize()
    vec_left2d = vec_left- vec_left.project(vec_up)
    vec_right2d.normalize()

    corner = get_corner_type(vec_up, vec_right2d, vec_left2d, threshold)
    if corner == 'FOLDING':
        vec_tangent = ZERO_VEC
    elif corner == 'STRAIGHT':
        if vec_right2d.length >= vec_left2d.length:
            vec_longer = vec_right2d
        else:
            vec_longer = -vec_left2d
        vec_tangent = vec_longer.cross(vec_up)
    elif corner == 'CONVEX':
        vec_tangent = vec_right2d + vec_left2d
        vec_tangent *= -1
    elif corner == 'CONCAVE':
        vec_tangent = vec_right2d + vec_left2d

    vec_tangent.normalize()

    return vec_tangent

def get_factor(vec_direction, vec_right, vec_left, func=max):
    if vec_direction == ZERO_VEC:
        return .0

    denominator = func(sin(vec_direction.angle(vec_right)), sin(vec_direction.angle(vec_left)))
    if denominator != .0:
        return 1.0 / denominator
    else:
        return .0

def collect_offset_edges(bm):
    set_offset_edges = set()
    for e in bm.edges:
        if e.select:
            co_faces_selected = 0
            for f in e.link_faces:
                if f.select:
                    co_faces_selected += 1
                    if co_faces_selected == 2:
                        break
            else:
                set_offset_edges.add(e)

    if not set_offset_edges:
        return None

    return set_offset_edges

def collect_loops(set_offset_edges):
    set_edges_copy = set_offset_edges.copy()

    loops = []  # [v, e, v, e, ... , e, v]
    while set_edges_copy:
        edge_start = set_edges_copy.pop()
        v_left, v_right = edge_start.verts
        lp = [v_left, edge_start, v_right]
        reverse = False
        while True:
            edge = None
            for e in v_right.link_edges:
                if e in set_edges_copy:
                    if edge:
                        # Overlap detected.
                        return None
                    edge = e
                    set_edges_copy.remove(e)
            if edge:
                v_right = edge.other_vert(v_right)
                lp.extend((edge, v_right))
                continue
            else:
                if v_right is v_left:
                    # Real loop.
                    loops.append(lp)
                    break
                elif reverse is False:
                    # Right side of half loop.
                    # Reversing the loop to operate same procedure on the left side.
                    lp.reverse()
                    v_right, v_left = v_left, v_right
                    reverse = True
                    continue
                else:
                    # Half loop, completed.
                    loops.append(lp)
                    break
    return loops

def reorder_loop(verts, edges, normal, adj_faces):
    for i, adj_f in enumerate(adj_faces):
        if adj_f is None:
            continue
        v1, v2 = verts[i], verts[i+1]
        e = edges[i]
        fv = tuple(adj_f.verts)
        if fv[fv.index(v1)-1] is v2:
            # Align loop direction
            verts.reverse()
            edges.reverse()
            adj_faces.reverse()
        if normal.dot(adj_f.normal) < .0:
            normal *= -1
        break
    return verts, edges, normal, adj_faces

def get_adj_ix(ix_start, vec_edges, half_loop):
    # Get adjacent edge index, skipping zero length edges
    len_edges = len(vec_edges)
    if half_loop:
        range_right = range(ix_start, len_edges)
        range_left = range(ix_start-1, -1, -1)
    else:
        range_right = range(ix_start, ix_start+len_edges)
        range_left = range(ix_start-1, ix_start-1-len_edges, -1)

    ix_right = ix_left = None
    for i in range_right:
        # Right
        i %= len_edges
        if vec_edges[i] != ZERO_VEC:
            ix_right = i
            break
    for i in range_left:
        # Left
        i %= len_edges
        if vec_edges[i] != ZERO_VEC:
            ix_left = i
            break
    if half_loop:
        # If index of one side is None, assign another index.
        if ix_right is None:
            ix_right = ix_left
        if ix_left is None:
            ix_left = ix_right

    return ix_right, ix_left

def get_normals(lp_normal, ix_r, ix_l, adj_faces):
    normal_r = normal_l = None
    if adj_faces:
        f_r, f_l = adj_faces[ix_r], adj_faces[ix_l]
        if f_r:
            normal_r = f_r.normal
        if f_l:
            normal_l = f_l.normal

    if normal_r and normal_l:
        vec_up = (normal_r + normal_l).normalized()
        if vec_up == ZERO_VEC:
            vec_up = lp_normal.copy()
    elif normal_r or normal_l:
        vec_up = (normal_r or normal_l).copy()
    else:
        vec_up = lp_normal.copy()

    return vec_up, normal_r, normal_l

def get_adj_faces(edges):
    adj_faces = []
    adj_exist = False
    for e in edges:
        face = None
        for f in e.link_faces:
            # Search an adjacent face.
            # Selected face has precedance.
            if not f.hide and f.normal != ZERO_VEC:
                face = f
                adj_exist = True
                if f.select: break
        adj_faces.append(face)
    if adj_exist:
        return adj_faces
    else:
        return None

def get_edge_rail(vert, set_offset_edges):
    co_edge =  0
    vec_inner = None
    for e in vert.link_edges:
        if not e.hide and e not in set_offset_edges:
            v_other = e.other_vert(vert)
            vec = v_other.co - vert.co
            if vec != ZERO_VEC:
                co_edge += 1
                vec_inner = vec
                if co_edge == 2:
                    return None
    else:
        return vec_inner

def get_cross_rail(vec_tan, vec_edge_r, vec_edge_l, normal_r, normal_l, threshold=1.0e-4):
    # Cross rail is a cross vector between normal_r and normal_l.
    angle = normal_r.angle(normal_l)
    if angle < threshold:
        # normal_r and normal_l are almost same, no cross vector.
        return None

    vec_cross = normal_r.cross(normal_l)
    vec_cross.normalize()
    if vec_cross.dot(vec_tan) < .0:
        vec_cross *= -1
    cos_min = min(vec_tan.dot(vec_edge_r), vec_tan.dot(vec_edge_l))
    cos = vec_tan.dot(vec_cross)
    if cos >= cos_min:
        return vec_cross
    else:
        return None

def do_offset(width, depth, verts, directions, geom_ex):
    if geom_ex:
        geom_s = geom_ex['side']
        verts_ex = []
        for v in verts:
            for e in v.link_edges:
                if e in geom_s:
                    verts_ex.append(e.other_vert(v))
                    break
        #assert len(verts) == len(verts_ex)
        verts = verts_ex

    for v, (t, u) in zip(verts, directions):
        v.co += width * t + depth * u

def extrude_edges(bm, set_offset_edges):
    extruded = bmesh.ops.extrude_edge_only(bm, edges=list(set_offset_edges))['geom']
    n_edges = n_faces = len(set_offset_edges)
    n_verts = len(extruded) - n_edges - n_faces

    geom = dict()
    geom['verts'] = verts = set(extruded[:n_verts])
    geom['edges'] = edges = set(extruded[n_verts:n_verts + n_edges])
    geom['faces'] = set(extruded[n_verts + n_edges:])
    geom['side'] = set(e for v in verts for e in v.link_edges if e not in edges)

    return geom

def clean(bm, mode, set_offset_edges, geom_ex=None):
    for f in bm.faces:
        f.select = False
    if geom_ex:
        for e in geom_ex['edges']:
            e.select = True
        if mode == 'offset':
            lis_geom = list(geom_ex['side']) + list(geom_ex['faces'])
            bmesh.ops.delete(bm, geom=lis_geom, context=2)
    else:
        for e in set_offset_edges:
            e.select = True

def collect_mirror_planes(edit_object):
    mirror_planes = []
    eob_mat_inv = edit_object.matrix_world.inverted()
    for m in edit_object.modifiers:
        if (m.type == 'MIRROR' and m.use_mirror_merge):
            merge_limit = m.merge_threshold
            if not m.mirror_object:
                loc = ZERO_VEC
                norm_x, norm_y, norm_z = X_UP, Y_UP, Z_UP
            else:
                mirror_mat_local = eob_mat_inv * m.mirror_object.matrix_world
                loc = mirror_mat_local.to_translation()
                norm_x, norm_y, norm_z, _ = mirror_mat_local.adjugated()
                norm_x = norm_x.to_3d().normalized()
                norm_y = norm_y.to_3d().normalized()
                norm_z = norm_z.to_3d().normalized()
            if m.use_x:
                mirror_planes.append((loc, norm_x, merge_limit))
            if m.use_y:
                mirror_planes.append((loc, norm_y, merge_limit))
            if m.use_z:
                mirror_planes.append((loc, norm_z, merge_limit))
    return mirror_planes

def get_vert_mirror_pairs(set_offset_edges, mirror_planes):
    if mirror_planes:
        set_edges_copy = set_offset_edges.copy()
        vert_mirror_pairs = dict()
        for e in set_offset_edges:
            v1, v2 = e.verts
            for mp in mirror_planes:
                p_co, p_norm, mlimit = mp
                v1_dist = abs(p_norm.dot(v1.co - p_co))
                v2_dist = abs(p_norm.dot(v2.co - p_co))
                if v1_dist <= mlimit:
                    # v1 is on a mirror plane.
                    vert_mirror_pairs[v1] = mp
                if v2_dist <= mlimit:
                    # v2 is on a mirror plane.
                    vert_mirror_pairs[v2] = mp
                if v1_dist <= mlimit and v2_dist <= mlimit:
                    # This edge is on a mirror_plane, so should not be offsetted.
                    set_edges_copy.remove(e)
        return vert_mirror_pairs, set_edges_copy
    else:
        return None, set_offset_edges

def get_mirror_rail(mirror_plane, vec_up):
    p_norm = mirror_plane[1]
    mirror_rail = vec_up.cross(p_norm)
    if mirror_rail != ZERO_VEC:
        # Project vec_up to mirror_plane
        vec_up = vec_up - vec_up.project(p_norm)
        vec_up.normalize()
        return mirror_rail, vec_up
    else:
        return None, vec_up

def get_verts_and_directions(lp, vec_upward, normal_fallback, vert_mirror_pairs, **options):
    # vec_front is used when loop normal couldn't calculated because the loop is straight.
    # vec_upward is used in order to unify all loop normals when follow_face is off.
    opt_follow_face = options['follow_face']
    opt_edge_rail = options['edge_rail']
    opt_er_only_end = options['edge_rail_only_end']
    opt_threshold = options['threshold']

    verts, edges = lp[::2], lp[1::2]
    set_edges = set(edges)
    lp_normal = calc_normal_from_verts(verts, fallback=normal_fallback)

    ##### Loop order might be changed below.
    if lp_normal.dot(vec_upward) < .0:
        # Keep consistent loop normal.
        verts.reverse()
        edges.reverse()
        lp_normal *= -1

    if opt_follow_face:
        adj_faces = get_adj_faces(edges)
        if adj_faces:
            verts, edges, lp_normal, adj_faces = \
                reorder_loop(verts, edges, lp_normal, adj_faces)
    else:
        adj_faces = None
    ##### Loop order might be changed above.

    vec_edges = [(e.other_vert(v).co - v.co).normalized() for v, e in zip(verts, edges)]

    if verts[0] is verts[-1]:
        # Real loop. Popping last vertex.
        verts.pop()
        HALF_LOOP = False
    else:
        # Half loop
        HALF_LOOP = True

    len_verts = len(verts)
    directions = []
    for i in range(len_verts):
        v = verts[i]
        if HALF_LOOP and (i == 0 or i == len_verts-1):
            VERT_END = True
        else:
            VERT_END = False
        ix_r, ix_l = get_adj_ix(i, vec_edges, HALF_LOOP)
        if ix_r is None:
            break
        vec_edge_r = vec_edges[ix_r]
        vec_edge_l = -vec_edges[ix_l]

        vec_up, normal_r, normal_l = get_normals(lp_normal, ix_r, ix_l, adj_faces)
        vec_tan = calc_tangent(vec_up, vec_edge_r, vec_edge_l, opt_threshold)

        if vec_tan != ZERO_VEC:
            # Project vec_tan to one of rail vector.
            rail = None
            if vert_mirror_pairs and VERT_END:
                if v in vert_mirror_pairs:
                    rail, vec_up = get_mirror_rail(vert_mirror_pairs[v], vec_up)
            if opt_edge_rail:
                # Get edge rail.
                # edge rail is a vector of inner edge.
                if (not opt_er_only_end) or VERT_END:
                    rail = get_edge_rail(v, set_edges)
            if (not rail) and normal_r and normal_l:
                # Get cross rail.
                # Cross rail is a cross vector between normal_r and normal_l.
                rail = get_cross_rail(vec_tan, vec_edge_r, vec_edge_l,
                                            normal_r, normal_l, opt_threshold)
            if rail:
                vec_tan = vec_tan.project(rail)
                vec_tan.normalize()
                # Make vec_up perpendicular to vec_tan.
                vec_up -= vec_up.project(rail)
                vec_up.normalize()

        vec_tan *= get_factor(vec_tan, vec_edge_r, vec_edge_l)
        vec_up *= get_factor(vec_up, vec_edge_r, vec_edge_l)
        directions.append((vec_tan, vec_up))

    if directions:
        return verts, directions
    else:
        return None, None


class OffsetEdges(bpy.types.Operator):
    """Offset Edges."""
    bl_idname = "mesh.offset_edges"
    bl_label = "Offset Edges"
    bl_options = {'REGISTER', 'UNDO'}

    geometry_mode = bpy.props.EnumProperty(
        items=[('offset', "Offset", "Offset edges"),
               ('extrude', "Extrude", "Extrude edges"),
               ('move', "Move", "Move selected edges")],
        name="Geometory mode", default='offset')
    width = bpy.props.FloatProperty(
        name="Width", default=.2, precision=4, step=1)
    flip_width = bpy.props.BoolProperty(
        name="Flip Width", default=False,
        description="Flip width direction")
    depth = bpy.props.FloatProperty(
        name="Depth", default=.0, precision=4, step=1)
    flip_depth = bpy.props.BoolProperty(
        name="Flip Depth", default=False,
        description="Flip depth direction")
    depth_mode = bpy.props.EnumProperty(
        items=[('angle', "Angle", "Angle"),
               ('depth', "Depth", "Depth")],
        name="Depth mode", default='angle')
    angle = bpy.props.FloatProperty(
        name="Angle", default=0, step=.1, min=-4*pi, max=4*pi,
        subtype='ANGLE', description="Angle")
    flip_angle = bpy.props.BoolProperty(
        name="Flip Angle", default=False,
        description="Flip Angle")
    follow_face = bpy.props.BoolProperty(
        name="Follow Face", default=False,
        description="Offset along faces around")
    mirror_modifier = bpy.props.BoolProperty(
        name="Mirror Modifier", default=False,
        description="Take into account of Mirror modifier")
    edge_rail = bpy.props.BoolProperty(
        name="Edge Rail", default=False,
        description="Align vertices along inner edges")
    edge_rail_only_end = bpy.props.BoolProperty(
        name="Edge Rail Only End", default=False,
        description="Apply edge rail to end verts only")
    threshold = bpy.props.FloatProperty(
        name="Threshold", default=1.0e-4, step=.1, subtype='ANGLE',
        description="Angle threshold which determines straight or folding edges",
        options={'HIDDEN'})

    @classmethod
    def poll(self, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'geometry_mode', text="")
        #layout.prop(self, 'geometry_mode', expand=True)

        row = layout.row(align=True)
        row.prop(self, 'width')
        row.prop(self, 'flip_width', icon='ARROW_LEFTRIGHT', icon_only=True)

        layout.prop(self, 'depth_mode', expand=True)
        if self.depth_mode == 'angle':
            d_mode = 'angle'
            flip = 'flip_angle'
        else:
            d_mode = 'depth'
            flip = 'flip_depth'
        row = layout.row(align=True)
        row.prop(self, d_mode)
        row.prop(self, flip, icon='ARROW_LEFTRIGHT', icon_only=True)

        layout.separator()

        layout.prop(self, 'follow_face')

        row = layout.row()
        row.prop(self, 'edge_rail')
        if self.edge_rail:
            row.prop(self, 'edge_rail_only_end', text="OnlyEnd", toggle=True)

        layout.prop(self, 'mirror_modifier')

    def execute(self, context):
        #time_start = perf_counter()

        edit_object = context.edit_object
        me = edit_object.data

        bpy.ops.object.editmode_toggle()
        bm = bmesh.new()
        bm.from_mesh(me)

        set_offset_edges = collect_offset_edges(bm)
        if set_offset_edges is None:
            self.report({'WARNING'},
                        "No edges selected.")
            bm.free()
            bpy.ops.object.editmode_toggle()
            return {'CANCELLED'}

        if self.mirror_modifier:
            mirror_planes = collect_mirror_planes(edit_object)
            vert_mirror_pairs, set_offset_edges = \
                get_vert_mirror_pairs(set_offset_edges, mirror_planes)

            if not set_offset_edges:
                self.report({'WARNING'},
                            "All selected edges are on mirror planes.")
        else:
            vert_mirror_pairs = None

        loops = collect_loops(set_offset_edges)
        if loops is None:
            self.report({'WARNING'},
                        "Overlap detected. Select non-overlap edge loops")
            bm.free()
            bpy.ops.object.editmode_toggle()
            return {'CANCELLED'}


        if self.depth_mode == 'angle':
            w = self.width if not self.flip_width else -self.width
            angle = self.angle if not self.flip_angle else -self.angle
            width = w * cos(angle)
            depth = w * sin(angle)
        else:
            width = self.width if not self.flip_width else -self.width
            depth = self.depth if not self.flip_depth else -self.depth

        vec_upward = (X_UP + Y_UP + Z_UP).normalized()
        # vec_upward is used to unify loop normals when follow_face is off.
        normal_fallback = Z_UP
        #normal_fallback = Vector(context.region_data.view_matrix[2][:3])
        # normal_fallback is used when loop normal cannot be calculated.

        if self.geometry_mode == 'move':
            geom_ex = None
        else:
            geom_ex = extrude_edges(bm, set_offset_edges)

        follow_face = self.follow_face
        edge_rail = self.edge_rail
        er_only_end = self.edge_rail_only_end
        threshold = self.threshold
        for lp in loops:
            verts, directions = get_verts_and_directions(
                lp, vec_upward, normal_fallback, vert_mirror_pairs,
                follow_face=follow_face, edge_rail=edge_rail,
                edge_rail_only_end=er_only_end, threshold=threshold)
            if verts:
                do_offset(width, depth, verts, directions, geom_ex)

        clean(bm, self.geometry_mode, set_offset_edges, geom_ex)

        bm.to_mesh(me)
        bm.free()
        bpy.ops.object.editmode_toggle()

        #print("Time of offset_edges: ", perf_counter() - time_start)
        return {'FINISHED'}

    def invoke(self, context, event):
        edit_object = context.edit_object
        me = edit_object.data
        bpy.ops.object.editmode_toggle()
        for p in me.polygons:
            if p.select:
                self.follow_face = True
                break
        bpy.ops.object.editmode_toggle()

        return self.execute(context)


def draw_item(self, context):
    self.layout.operator_context = 'INVOKE_DEFAULT'
    self.layout.operator_menu_enum('mesh.offset_edges', 'geometry_mode')


def register():
    bpy.utils.register_class(OffsetEdges)
    bpy.types.VIEW3D_MT_edit_mesh_edges.append(draw_item)


def unregister():
    bpy.utils.unregister_class(OffsetEdges)
    bpy.types.VIEW3D_MT_edit_mesh_edges.remove(draw_item)


if __name__ == '__main__':
    register()

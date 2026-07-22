import math

import FreeCAD
import numpy as np
import Part

from .buildSolidCell import BuildSolid
from .fuseSolid import FuseSolid
from .remh import Cline
from .Utils.booleanFunction import BoolSequence, outer_terms
from .Utils.boundBox import solid_plane_box, myBox, BoxSettings


class CadCell:
    def __init__(self, stringCell: str = None, settings: BoxSettings = BoxSettings()):

        self.settings = settings
        self.boundBox = None

        if not stringCell:
            self.surfaces = {}
            self.surfaceList = []
            self.shape = None
            # self.likeCell = None
            self.definition = None
            self.name = 0
            # self.TRCL     = None  # cell transformacion "like-but" cells
            self.TRFL = None  # Universe transformation in fill Universe
            self.U = -1  # Cell Universe number
            self.FILL = 0  # Fill Universe number
            self.MAT = 0  # material number
            self.CurrentTR = None
            self.level = None
            self.__defTerms__ = None
            self.__operator__ = None
            self.externalBox = None
            self.solid_plane = None
            self.boundBox = None
            self.latticeBox = None

        else:
            self.surfaces = None
            self.shape = None
            self.name = stringCell.name
            self.TRFL = stringCell.TR  # Universe transformation in fill Universe
            self.U = stringCell.U  # Cell Universe number
            self.FILL = stringCell.FILL  # Fill Universe number
            self.MAT = stringCell.MAT  # material number
            self.CurrentTR = self.TRFL
            self.level = None

            self.__defTerms__ = None
            self.__operator__ = None
            self.__setDefinition__(stringCell)
            if self.definition is None:
                self.surfaceList = []
            else:
                self.surfaceList = self.definition.get_surfaces_numbers()
            self.externalBox = None
            self.latticeBox = getattr(stringCell, "externalBox", None)
            self.solid_plane = None
            self.boundBox = None

    def copy(self):
        cpCell = CadCell(settings=self.settings)
        if self.solid_plane is not None:
            cpCell.solid_plane = self.solid_plane.copy()
        cpCell.surfaceList = self.surfaceList[:]
        cpCell.externalBox = self.externalBox
        cpCell.boundBox = self.boundBox
        cpCell.latticeBox = self.latticeBox
        cpCell.surfaces = {}
        if self.surfaces is not None:
            for name, s in self.surfaces.items():
                cpCell.surfaces[name] = s.copy()

        if type(self.definition) is Cline:
            cpCell.definition = Cline(self.definition.str)

        elif type(self.definition) is BoolSequence:
            cpCell.definition = self.definition.copy()

        else:
            cpCell.definition = None

        cpCell.name = self.name
        cpCell.TRFL = self.TRFL
        cpCell.U = self.U
        cpCell.FILL = self.FILL
        cpCell.MAT = self.MAT
        cpCell.level = self.level

        if self.CurrentTR is not None:
            cpCell.CurrentTR = self.CurrentTR.submatrix(4)

        if self.shape is not None:
            cpCell.shape = self.shape.copy()

        return cpCell

    def getSubCell(self, seq):

        subCell = self.copy()
        subCell.definition = seq.copy()
        subCell.shape = None
        subCell.boundBox = None

        subCell.surfaceList = subCell.definition.get_surfaces_numbers()
        for s in tuple(subCell.surfaces.keys()):
            if s not in subCell.surfaceList:
                del subCell.surfaces[s]

        return subCell

    def getOuterTerms(self):
        if self.definition is None:
            return None, None
        if not self.__defTerms__:
            self.__defTerms__, self.__operator__ = outer_terms(self.definition.str)
        return self.__defTerms__, self.__operator__

    def makeBox(self):
        if self.boundBox.Orientation == "Forward":
            if self.boundBox.Box is None:
                boundBox = self.externalBox.Box
            else:
                boundBox = self.boundBox.Box
        else:
            boundBox = self.externalBox.Box
        box_origin = FreeCAD.Vector(boundBox.XMin, boundBox.YMin, boundBox.ZMin)
        if boundBox.XLength < 1e-6 or boundBox.YLength < 1e-6 or boundBox.ZLength < 1e-6:
            return None
        else:
            return Part.makeBox(boundBox.XLength, boundBox.YLength, boundBox.ZLength, box_origin)

    def build_BoundBox(self, externalBox=None, enlarge=0):

        if externalBox:
            outBox = externalBox
            self.externalBox = externalBox
        elif self.externalBox:
            outBox = self.externalBox
        else:
            r = self.settings.universe_radius
            outBox = myBox(FreeCAD.BoundBox(-r, -r, -r, r, r, r), "Forward")
            self.externalBox = outBox

        if outBox.Box is None:
            self.boundBox = outBox
        elif self.definition is None:
            self.boundBox = outBox
        else:
            if self.solid_plane is None:
                self.solid_plane = solid_plane_box(self, outbox=outBox)
            elif not self.solid_plane.outBox.sameBox(outBox):
                self.solid_plane = solid_plane_box(self, outbox=outBox)
            self.boundBox = self.solid_plane.get_boundBox(enlarge=enlarge)

    def buildShape(self, force=False, surfTR=None, simplify=False, fuse=False):

        if self.shape is not None and not force:
            return
        if surfTR:
            self.transformSurfaces(surfTR)

        cutShape = BuildSolid(self)
        self.shape = FuseSolid(cutShape)

    def buildSurfaceShape(self, boundBox):
        for s in self.surfaces.values():
            s.buildShape(boundBox)

    def transformSolid(self, matrix, reverse=False):
        if not self.shape:
            return
        shape = self.shape.copy()
        if reverse:
            shape.transformShape(matrix.inverse())
        else:
            shape.transformShape(matrix)
        self.shape = shape

    def transformSurfaces(self, matrix):
        for s in self.surfaces.values():
            s.transform(matrix)

    def setSurfaces(self, Surfaces):
        if self.surfaces is not None:
            return
        self.surfaces = {}
        for s in self.surfaceList:
            self.surfaces[s] = Surfaces[s]

    def cleanUndefined(self):
        if self.definition is None:
            return
        undefined = []
        for s in self.definition.get_surfaces_numbers():
            if self.surfaces[s].params is None:
                undefined.append(s)
        if undefined:
            self.definition.removeSurface(undefined)

        for s in undefined:
            del self.surfaces[s]

    def __setDefinition__(self, stringCell):

        self.definition = stringCell.geom
        if self.definition is None:
            return
        self.definition.remove_comments(full=True)
        self.definition.remove_cr()
        self.definition.remove_multispace()
        self.definition.remove_redundant()


class Plane:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "plane"
        self.id = Id
        self.shape = None
        self.params = params
        if tr:
            self.transform(tr)

    def __str__(self):
        return f"plane : {self.id}\nParameters : {self.params}"

    def copy(self):
        return Plane(self.label, self.id, self.params)

    def transform(self, matrix):
        v, d = self.params
        p = d * v  # vector p is d*plane normal
        v = matrix.submatrix(3).multVec(v)
        v.normalize()
        d = matrix.multVec(p) * v
        self.params = (v, d)

    def buildShape(self, boundBox):
        normal, p0 = self.params
        Box = FreeCAD.BoundBox(boundBox)
        Box.enlarge(10)

        pointEdge = []
        for i in range(12):
            edge = Box.getEdge(i)
            p1 = normal.dot(edge[0])
            p2 = normal.dot(edge[1])
            d0 = p0 - p1
            d1 = p2 - p1
            if d1 != 0:
                a = d0 / d1
                if a >= 0 and a <= 1:
                    pointEdge.append(edge[0] + a * (edge[1] - edge[0]))

        if len(pointEdge) == 0:
            self.shape = None
            return
        s = FreeCAD.Vector((0, 0, 0))
        for v in pointEdge:
            s = s + v
        s = s / len(pointEdge)

        vtxvec = []
        for v in pointEdge:
            vtxvec.append(v - s)

        X0 = vtxvec[0]
        Y0 = normal.cross(X0)

        orden = []
        for i, v in enumerate(vtxvec):
            phi = np.arctan2(v.dot(Y0), v.dot(X0))
            orden.append((phi, i))
        orden.sort()

        self.shape = Part.Face(Part.makePolygon([pointEdge[p[1]] for p in orden], True))


class Sphere:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "sphere"
        self.id = Id
        self.shape = None
        self.params = params
        if params[1] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[1]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return Sphere(self.label, self.id, self.params)

    def transform(self, matrix):
        p, R = self.params
        p = matrix.multVec(p)
        self.params = (p, R)

    def buildShape(self, boundBox):
        origin, R = self.params
        self.shape = Part.makeSphere(R, origin)


class Cylinder:
    def __init__(self, label, Id, params, tr=None, truncated=False):
        self.label = label
        self.type = "cylinder"
        self.id = Id
        self.shape = None
        self.params = params
        self.truncated = truncated
        if params[2] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return Cylinder(self.label, self.id, self.params, truncated=self.truncated)

    def transform(self, matrix):
        p, v, R = self.params
        v = matrix.submatrix(3).multVec(v)
        p = matrix.multVec(p)
        self.params = (p, v, R)

    def buildShape(self, boundBox):

        p, vec, r = self.params

        if not self.truncated:
            dmin = vec.dot(boundBox.getPoint(0) - p)
            dmax = dmin
            for i in range(1, 8):
                d = vec.dot(boundBox.getPoint(i) - p)
                dmin = min(d, dmin)
                dmax = max(d, dmax)

            height = dmax - dmin
            dmin -= 0.1 * height
            dmax += 0.1 * height
            height = dmax - dmin

            point = p + dmin * vec
            self.shape = Part.makeCylinder(r, height, point, vec, 360)
            # self.shape = makeCylinder2( r,height,point,vec)
        else:
            self.shape = Part.makeCylinder(r, vec.Length, p, vec, 360)
            # self.shape = Part.makeCylinder2( r,vec.Length,p,vec)

        return


class Cone:
    def __init__(self, label, Id, params, tr=None, truncated=False):
        self.label = label
        self.type = "cone"
        self.id = Id
        self.shape = None
        self.params = params
        self.truncated = truncated
        # if params[2] <= 0:
        #    print(f"{self.type} surface {label} has a zero semi-angle value.")
        if tr:
            self.transform(tr)

    def copy(self):
        return Cone(self.label, self.id, self.params, truncated=self.truncated)

    def transform(self, matrix):
        if not self.truncated:
            p, v, t, dbl = self.params
            v = matrix.submatrix(3).multVec(v)
            p = matrix.multVec(p)
            self.params = (p, v, t, dbl)
        else:
            p, v, r1, r2 = self.params
            v = matrix.submatrix(3).multVec(v)
            p = matrix.multVec(p)
            self.params = (p, v, r1, r2)

    def buildShape(self, boundBox):
        if not self.truncated:
            apex, axis, t, dblsht = self.params

            dmin = axis.dot(boundBox.getPoint(0) - apex)
            dmax = dmin
            for i in range(1, 8):
                d = axis.dot(boundBox.getPoint(i) - apex)
                dmin = min(d, dmin)
                dmax = max(d, dmax)

            length = max(abs(dmin), abs(dmax))
            R = length * t
            OneSheetCone = Part.makeCone(0, R, length, apex, axis, 360)
            if not dblsht:
                self.shape = OneSheetCone
            else:
                OtherSheet = Part.makeCone(0, R, length, apex, -axis, 360)
                DoubleSheetCone = OneSheetCone.fuse([OtherSheet])
                DoubleSheetCone.removeSplitter()
                self.shape = DoubleSheetCone
        else:
            center, axis, r1, r2 = self.params
            self.shape = Part.makeCone(r1, r2, axis.Length, center, axis, 360)


class EllipticCone:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "cone_elliptic"
        self.id = Id
        self.shape = None
        self.params = params
        if params[3][0] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[3][0]}")
        if params[3][1] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[3][1]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return EllipticCone(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v, ra, radii, raxes, dbl = self.params
        v = matrix.submatrix(3).multVec(v)
        raxes[0] = matrix.submatrix(3).multVec(raxes[0])
        raxes[1] = matrix.submatrix(3).multVec(raxes[1])
        p = matrix.multVec(p)
        self.params = (p, v, ra, radii, raxes, dbl)

    def buildShape(self, boundBox):
        apex, axis, ra, radii, raxes, dblsht = self.params

        dmin = axis.dot(boundBox.getPoint(0) - apex)
        dmax = dmin
        for i in range(1, 8):
            d = axis.dot(boundBox.getPoint(i) - apex)
            dmin = min(d, dmin)
            dmax = max(d, dmax)

        length = max(abs(dmin), abs(dmax))
        OneSheetCone = makeEllipticCone(apex, axis, ra, radii, raxes, length)
        if not dblsht:
            self.shape = OneSheetCone
        else:
            OtherSheet = makeEllipticCone(apex, -axis, ra, radii, raxes, length)
            DoubleSheetCone = OneSheetCone.fuse([OtherSheet])
            DoubleSheetCone.removeSplitter()
            self.shape = DoubleSheetCone


class Hyperboloid:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "hyperboloid"
        self.id = Id
        self.shape = None
        self.params = params
        if params[2][0] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][0]}")
        if params[2][1] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][1]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return Hyperboloid(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v, radii, raxes, onesht = self.params
        v = matrix.submatrix(3).multVec(v)
        raxes[0] = matrix.submatrix(3).multVec(raxes[0])
        raxes[1] = matrix.submatrix(3).multVec(raxes[1])
        p = matrix.multVec(p)
        self.params = (p, v, radii, raxes, onesht)

    def buildShape(self, boundBox):
        center, axis, radii, rAxes, onesht = self.params

        dmin = axis.dot(boundBox.getPoint(0) - center)
        dmax = dmin
        for i in range(1, 8):
            d = axis.dot(boundBox.getPoint(i) - center)
            dmin = min(d, dmin)
            dmax = max(d, dmax)

        length = max(abs(dmin), abs(dmax))
        self.shape = makeHyperboloid(center, radii, rAxes, axis, onesht, length)


class Ellipsoid:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "ellipsoid"
        self.id = Id
        self.shape = None
        self.params = params
        if params[2][0] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][0]}")
        if params[2][1] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][1]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return Ellipsoid(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v, radii, raxes = self.params
        v = matrix.submatrix(3).multVec(v)
        raxes[0] = matrix.submatrix(3).multVec(raxes[0])
        raxes[1] = matrix.submatrix(3).multVec(raxes[1])
        p = matrix.multVec(p)
        self.params = (p, v, radii, raxes)

    def buildShape(self, boundBox):
        center, axis, radii, rAxes = self.params
        self.shape = makeEllipsoid(center, radii, rAxes, axis)


class EllipticCylinder:
    def __init__(self, label, Id, params, tr=None, truncated=False):
        self.label = label
        self.type = "cylinder_elliptic"
        self.id = Id
        self.shape = None
        self.params = params
        self.truncated = truncated
        if params[2][0] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][0]}")
        if params[2][1] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][1]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return EllipticCylinder(self.label, self.id, self.params, truncated=self.truncated)

    def transform(self, matrix):
        p, v, radii, raxes = self.params
        v = matrix.submatrix(3).multVec(v)
        raxes[0] = matrix.submatrix(3).multVec(raxes[0])
        raxes[1] = matrix.submatrix(3).multVec(raxes[1])
        p = matrix.multVec(p)
        self.params = (p, v, radii, raxes)

    def buildShape(self, boundBox):
        center, axis, radii, rAxes = self.params
        if not self.truncated:
            dmin = axis.dot(boundBox.getPoint(0) - center)
            dmax = dmin
            for i in range(1, 8):
                d = axis.dot(boundBox.getPoint(i) - center)
                dmin = min(d, dmin)
                dmax = max(d, dmax)

            height = dmax - dmin
            dmin -= 0.1 * height
            dmax += 0.1 * height
            height = dmax - dmin
            point = center + dmin * axis

            self.shape = makeEllipticCylinder(point, radii, rAxes, axis, height)
        else:
            height = axis.Length
            self.shape = makeEllipticCylinder(center, radii, rAxes, axis / height, height)


class HyperbolicCylinder:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "cylinder_hyperbolic"
        self.id = Id
        self.shape = None
        self.params = params
        if params[2][0] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][0]}")
        if params[2][1] <= 0:
            print(f"{self.type} surface {label} has a bad radius value: {params[2][1]}")

        if tr:
            self.transform(tr)

    def copy(self):
        return HyperbolicCylinder(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v, radii, raxes = self.params
        v = matrix.submatrix(3).multVec(v)
        raxes[0] = matrix.submatrix(3).multVec(raxes[0])
        raxes[1] = matrix.submatrix(3).multVec(raxes[1])
        p = matrix.multVec(p)
        self.params = (p, v, radii, raxes)

    def buildShape(self, boundBox):
        center, axis, radii, rAxes = self.params

        dmin = axis.dot(boundBox.getPoint(0) - center)
        dmax = dmin
        for i in range(1, 8):
            d = axis.dot(boundBox.getPoint(i) - center)
            dmin = min(d, dmin)
            dmax = max(d, dmax)

        height = dmax - dmin
        dmin -= 0.1 * height
        dmax += 0.1 * height
        height = dmax - dmin
        point = center + dmin * axis

        self.shape = makeHyperbolicCylinder(point, radii, rAxes, axis, height)


class ParabolicCylinder:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "cylinder_parabolic"
        self.id = Id
        self.shape = None
        self.params = params
        if params[4] <= 0:
            print(f"{self.type} surface {label} has a bad focal value: {params[4]}")
        if tr:
            self.transform(tr)

    def copy(self):
        return ParabolicCylinder(self.label, self.id, self.params)

    def transform(self, matrix):
        vertex, free_axis, opening_axis, curvature_axis, focal = self.params
        rotation = matrix.submatrix(3)
        self.params = (
            matrix.multVec(vertex),
            rotation.multVec(free_axis),
            rotation.multVec(opening_axis),
            rotation.multVec(curvature_axis),
            focal,
        )

    def buildShape(self, boundBox):
        self.shape = makeParabolicCylinder(*self.params, boundBox)


class Paraboloid:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "paraboloid"
        self.id = Id
        self.shape = None
        self.params = params
        if params[2] == 0:
            print(f"{self.type} surface {label} has a zero focal")

        if tr:
            self.transform(tr)

    def copy(self):
        return Paraboloid(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v, focal = self.params
        v = matrix.submatrix(3).multVec(v)
        p = matrix.multVec(p)
        self.params = (p, v, focal)

    def buildShape(self, boundBox):
        center, axis, focal = self.params
        axis.normalize()

        dist = []
        for i in range(8):
            d = axis.dot(boundBox.getPoint(i) - center)
            dist.append(d)
        dist.sort()
        dmin, dmax = dist[0], dist[-1]
        if dmax <= 0:
            return
        if dmin < 0:
            dmin = 0

        rmin = math.sqrt(4 * focal * dmin)
        rmax = math.sqrt(4 * focal * dmax)

        if (rmax - rmin) / rmax < 0.01:
            r = 0.5 * (rmin + rmax)
            self.shape = Part.makeCylinder(r, dmax, center, axis, 360)
        else:
            self.shape = makeParaboloid(center, axis, focal, dmax)


class Torus:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "torus"
        self.id = Id
        self.shape = None
        self.params = params
        if params[2] < 0:
            print(f"{self.type} surface {label} has a negative major radius: {params[2]}")
        if params[3] <= 0:
            print(f"{self.type} surface {label} has a bad minor radius a value: {params[3]}")
        if params[4] <= 0:
            print(f"{self.type} surface {label} has a bad minor radius b value: {params[4]}")

        if tr:
            self.transform(tr)

    def copy(self):
        return Torus(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v, Ra, Rb, Rc = self.params
        v = matrix.submatrix(3).multVec(v)
        p = matrix.multVec(p)
        self.params = (p, v, Ra, Rb, Rc)

    def buildShape(self, boundBox):
        center, axis, Ra, Rb, Rc = self.params  # Ra distance from torus axis; R radius of toroidal-cylinder
        if (abs(Rb - Rc) < 1e-5) and Ra > 0:
            self.shape = Part.makeTorus(Ra, Rb, center, axis)  # FreeCAD circular Torus
        else:
            self.shape = makeEllipticTorus(Ra, Rb, Rc, center, axis)  # Home made elliptic Torus


class Box:
    def __init__(self, label, Id, params, tr=None):
        self.label = label
        self.type = "box"
        self.id = Id
        self.shape = None
        self.params = params
        if params[1].Length <= 0:
            print(f"{self.type} surface {label} has a bad X dimension: {params[1]}")
        if params[2].Length <= 0:
            print(f"{self.type} surface {label} has a bad Y dimension: {params[2]}")
        if params[3].Length <= 0:
            print(f"{self.type} surface {label} has a bad Z dimension: {params[3]}")

        if tr:
            self.transform(tr)

    def copy(self):
        return Box(self.label, self.id, self.params)

    def transform(self, matrix):
        p, v1, v2, v3 = self.params
        p = matrix.multVec(p)
        v1 = matrix.multVec(v1)
        v2 = matrix.multVec(v2)
        v3 = matrix.multVec(v3)
        self.params = (p, v1, v2, v3)

    def buildShape(self, boundBox):
        p, v1, v2, v3 = self.params
        a1 = FreeCAD.Vector(v1)
        a2 = FreeCAD.Vector(v2)
        a3 = FreeCAD.Vector(v3)
        a1.normalize()
        a2.normalize()
        a3.normalize()

        m = FreeCAD.Matrix(
            a1.x,
            a2.x,
            a3.x,
            p.x,
            a1.y,
            a2.y,
            a3.y,
            p.y,
            a1.z,
            a2.z,
            a3.z,
            p.z,
            0,
            0,
            0,
            1,
        )
        box = Part.makeBox(v1.Length, v2.Length, v3.Length)
        self.shape = box.transformGeometry(m)


class Undefined:
    def __init__(self, Id):
        self.type = "Undefined"
        self.id = Id
        self.shape = None
        self.params = None

    def copy(self):
        return Undefined(self.id)

    def buildShape(self, boundBox):
        return

    def transform(self, matrix):
        return


def makeHyperboloid(center, radii, rAxes, axis, onesht, length):
    if onesht:
        radial_axis = rAxes[0]
        longitudinal_axis = rAxes[1]
        a = radii[0]  # transverse/radial radius
        c = radii[1]  # conjugate/longitudinal radius

        if a > c:
            # Preserve an exact conic whenever FreeCAD accepts the transverse
            # and conjugate radii in their geometrically correct order.
            S1 = center + radial_axis * a
            S2 = center + longitudinal_axis * c
            hyperbola = Part.Hyperbola(S1, S2, center)
            z = length
            radial_distance = a * math.sqrt(1.0 + (z / c) ** 2)
            point = center + radial_distance * radial_axis + z * longitudinal_axis
            parameter = abs(hyperbola.parameter(point))
            shape = hyperbola.toBSpline(-parameter, parameter).toShape(
                -parameter, parameter
            )
        else:
            # FreeCAD's three-point Hyperbola constructor rejects valid a < c
            # cases with "Axis value is invalid".  Interpolate the positive
            # branch r^2/a^2 - z^2/c^2 = 1 as a B-spline, then use the same
            # exact 360-degree revolve and end-cap operations as above.
            points = []
            sample_count = 101
            for index in range(sample_count):
                z = -length + 2.0 * length * index / (sample_count - 1)
                radial_distance = a * math.sqrt(1.0 + (z / c) ** 2)
                points.append(
                    center
                    + radial_distance * radial_axis
                    + z * longitudinal_axis
                )
            profile = Part.BSplineCurve()
            profile.interpolate(points)
            shape = profile.toShape()

        hyperFace = shape.revolve(center, longitudinal_axis, 360)

        StartPoint = hyperFace.Surface.BasisCurve.StartPoint - center
        EndPoint = hyperFace.Surface.BasisCurve.EndPoint - center

        rad1 = abs(StartPoint.dot(radial_axis))
        hgt1 = StartPoint.dot(longitudinal_axis)
        cc1 = center + longitudinal_axis * hgt1
        circle1 = Part.Circle(cc1, -longitudinal_axis, rad1).toShape()
        cFace1 = Part.makeFace(circle1, "Part::FaceMakerSimple")

        rad2 = abs(EndPoint.dot(radial_axis))
        hgt2 = EndPoint.dot(longitudinal_axis)
        cc2 = center + longitudinal_axis * hgt2
        circle2 = Part.Circle(cc2, longitudinal_axis, rad2).toShape()
        cFace2 = Part.makeFace(circle2, "Part::FaceMakerSimple")

        shell = Part.makeShell((cFace1, hyperFace, cFace2))
        hyperboloid = Part.makeSolid(shell)
    else:
        radial_radius = radii[0]
        axial_radius = radii[1]
        radial_axis = rAxes[0]
        axial_axis = rAxes[1]

        # Construct the positive branch of
        # x^2 / axial_radius^2 - y^2 / radial_radius^2 = 1.
        # Keep it as an exact conic: converting the curve to a B-spline can
        # move its vertex slightly off the revolution axis and make OCC's
        # full 360-degree revolution fail with "BRep_API: command not done".
        axial_point = center + axial_axis * axial_radius
        radial_point = center + radial_axis * radial_radius
        hyperbola = Part.Hyperbola(axial_point, radial_point, center)
        axial_axis = hyperbola.XAxis

        # `length` is obtained by projecting the bounding box onto the
        # hyperboloid axis, so it is an axial extent.  Extend the profile a
        # little beyond that box to avoid coincident Boolean boundaries, and
        # ensure it extends beyond the zero-radius vertex.
        padding = 1.0e-6 * max(length, 1.0)
        axial_extent = max(
            length + padding,
            axial_radius * (1.0 + 1.0e-9),
        )
        parameter = math.acosh(axial_extent / axial_radius)
        curve_edge = hyperbola.toShape(0.0, parameter)

        # Close the meridional section along the cap and revolution axis.
        # Revolving this face directly produces a solid and avoids separately
        # sewing a lateral face and cap at the degenerate apex.
        apex = curve_edge.Vertexes[0].Point
        rim = curve_edge.Vertexes[-1].Point
        cap_center = center + axial_axis * axial_extent
        radial_edge = Part.makeLine(rim, cap_center)
        axial_edge = Part.makeLine(cap_center, apex)
        profile_wire = Part.Wire((curve_edge, radial_edge, axial_edge))
        profile_face = Part.Face(profile_wire)
        hyper1 = profile_face.revolve(center, axial_axis, 360)

        if hyper1.isNull() or not hyper1.isValid():
            raise RuntimeError(
                "Failed to construct the positive sheet of a two-sheet "
                f"hyperboloid: radii={radii}, length={length}"
            )

        hyper2 = hyper1.rotated(center, hyperbola.YAxis, 180)
        hyperboloid = Part.makeCompound((hyper1, hyper2))

    return hyperboloid


def makeHyperbolicCylinder(center, radii, rAxes, axis, length):

    S11 = center + rAxes[1] * radii[1]  # major axis
    S12 = center + rAxes[0] * radii[0]  # minor axis
    S21 = center - rAxes[1] * radii[1]  # major axis
    S22 = center - rAxes[0] * radii[0]  # minor axis

    hyperbola1 = Part.Hyperbola(S11, S12, center)
    hyperbola2 = Part.Hyperbola(S21, S22, center)
    d = axis * length

    Y = length
    X = radii[1] * math.sqrt((Y / radii[0]) ** 2 + 1)
    point = center + X * rAxes[1] + Y * rAxes[0]  # point in taken as length is always counted on minor axis
    parameter = abs(hyperbola1.parameter(point))

    shape1 = hyperbola1.toBSpline(-parameter, parameter).toShape(-parameter, parameter)
    shape2 = hyperbola2.toBSpline(-parameter, parameter).toShape(-parameter, parameter)
    surf1 = shape1.extrude(d)
    surf2 = shape2.extrude(d)

    return Part.makeCompound((surf1, surf2))


def makeParabolicCylinder(vertex, free_axis, opening_axis, curvature_axis, focal, boundBox):
    """Build a bounded parabolic-cylinder cutting solid.

    The parabola is closed by a chord beyond the cell box, then extruded along
    its free axis.  The artificial chord and end caps therefore cannot alter
    the cell being cut; only the parabolic wall intersects it.
    """
    free_axis = FreeCAD.Vector(free_axis)
    opening_axis = FreeCAD.Vector(opening_axis)
    curvature_axis = FreeCAD.Vector(curvature_axis)
    free_axis.normalize()
    opening_axis.normalize()
    curvature_axis.normalize()

    free_distances = [free_axis.dot(boundBox.getPoint(i) - vertex) for i in range(8)]
    dmin, dmax = min(free_distances), max(free_distances)
    axial_margin = 0.1 * max(dmax - dmin, 1.0)
    dmin -= axial_margin
    dmax += axial_margin

    opening_distances = [opening_axis.dot(boundBox.getPoint(i) - vertex) for i in range(8)]
    opening_min, opening_max = min(opening_distances), max(opening_distances)
    opening_margin = 0.1 * max(opening_max - opening_min, 1.0)
    length = max(opening_max + opening_margin, opening_margin)
    radius = math.sqrt(4.0 * focal * length)

    base_vertex = vertex + dmin * free_axis
    parabola = Part.Parabola()
    parabola.Center = base_vertex
    parabola.XAxis = opening_axis
    # FreeCAD's Axis is the normal of the parabola's plane.  The free
    # cylinder axis is therefore both that normal and the extrusion direction.
    parabola.Axis = free_axis
    parabola.Focal = focal

    point = base_vertex + length * opening_axis + radius * curvature_axis
    parameter = abs(parabola.parameter(point))
    edge = parabola.toBSpline(-parameter, parameter).toShape(-parameter, parameter)
    chord = Part.makeLine(edge.Vertexes[-1].Point, edge.Vertexes[0].Point)
    face = Part.Face(Part.Wire([edge, chord]))
    return face.extrude((dmax - dmin) * free_axis)


def makeEllipticCylinder(center, radii, rAxes, axis, length):

    S1 = center + rAxes[1] * radii[1]  # major axis
    S2 = center + rAxes[0] * radii[0]  # minor axis
    d = axis * length

    ellipse = Part.Ellipse(S1, S2, center)
    ellipse2 = Part.Ellipse(S1 + d, S2 + d, center + d)

    shape = ellipse.toBSpline().toShape()
    shape2 = ellipse2.toBSpline().toShape()
    shell = Part.makeLoft([shape, shape2], True)
    return Part.makeSolid(shell)


def makeEllipsoid(center, radii, rAxes, axis):

    S1 = center + rAxes[1] * radii[1]  # major axis
    S2 = center + rAxes[0] * radii[0]  # minor axis

    ellipse = Part.Ellipse(S1, S2, center)

    if axis.add(-rAxes[0]).Length < 1e-5:
        shape = ellipse.toBSpline().toShape()
        shell = shape.revolve(center, axis, 180)
    else:
        shape = ellipse.toBSpline(0, math.pi).toShape(0, math.pi)
        shell = shape.revolve(center, axis, 360)
    return Part.makeSolid(shell)


def makeEllipticTorus(R, RZ, RX, center, ZAxis):

    rMaj = RZ
    rMin = RX
    XAxis = ortoVect(ZAxis)

    majorAxis = ZAxis
    minorAxis = XAxis
    if rMaj < rMin:
        rMaj, rMin = rMin, rMaj
        majorAxis, minorAxis = minorAxis, majorAxis

    eCenter = center + R * XAxis
    S1 = eCenter + majorAxis * rMaj  # major axis
    S2 = eCenter + minorAxis * rMin  # minor axis

    ellipse = Part.Ellipse(S1, S2, eCenter)
    if abs(R) < RX:  # degenerated Torus
        pz = RZ * math.sqrt(1 - (R / RX) ** 2)
        pz1 = center - pz * ZAxis
        pz2 = center + pz * ZAxis

        p1 = ellipse.parameter(pz1)
        p2 = ellipse.parameter(pz2)
        if p2 < p1:
            p2 += 2 * math.pi
        shape = ellipse.toBSpline(p1, p2).toShape(p1, p2)  # revolution around Major axis
        rev = shape.revolve(center, ZAxis, 360)
    else:
        shape = ellipse.toBSpline().toShape()  # revolution around Minor axis
        rev = shape.revolve(center, ZAxis, 360)
    shell = Part.makeShell((rev,))
    return Part.makeSolid(shell)


def makeParaboloid(center, axis, focal, length):

    parabola = Part.Parabola()
    parabola.Center = center
    parabola.Axis = ortoVect(axis)
    parabola.XAxis = axis
    parabola.Focal = focal

    R = math.sqrt(4 * focal * length)
    point = center + length * parabola.XAxis + R * parabola.YAxis
    parameter = abs(parabola.parameter(point))

    shape = parabola.toBSpline(0, parameter).toShape(0, parameter)
    paraFace = shape.revolve(center, axis, 360)

    cc = center + length * parabola.XAxis
    circle = Part.Circle(cc, -axis, R).toShape()
    cFace = Part.makeFace(circle, "Part::FaceMakerSimple")

    shell = Part.makeShell((cFace, paraFace))
    return Part.makeSolid(shell)


def makeEllipticCone(apex, axis, Ra, radii, rAxes, length):

    S1 = apex + rAxes[1] * radii[1] / Ra * length  # major axis
    S2 = apex + rAxes[0] * radii[0] / Ra * length  # minor axis
    d = axis * length

    point = Part.Point(apex).toShape()
    ellipse = Part.Ellipse(S1 + d, S2 + d, apex + d)

    shape = ellipse.toBSpline().toShape()
    shell = Part.makeLoft([point, shape], True)
    return Part.makeSolid(shell)


def ortoVect(v):
    vmax = 0
    vOrto = None
    if abs(v.x) > vmax:
        vOrto = (0, 1, 0)
        vmax = abs(v.x)
    if abs(v.y) > vmax:
        vOrto = (0, 0, 1)
        vmax = abs(v.y)
    if abs(v.z) > vmax:
        vOrto = (1, 0, 0)
        vmax = abs(v.z)

    if vOrto is None:
        return None

    vOrto = v.cross(FreeCAD.Vector(vOrto))
    vOrto.normalize()
    return vOrto

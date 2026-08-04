import math

import BOPTools.SplitAPI
import FreeCAD
import Part

from .fuseSolid import FuseSolid, TopoWrapper
from .data_class import Options


class SplitBase:
    def __init__(self, base, knownSurf=None, orientation="Forward"):
        self.base = base if base is None or isinstance(base, TopoWrapper) else TopoWrapper(base)
        self.knownSurf = {} if knownSurf is None else knownSurf
        self.orientation = orientation


def _perturbSurface(surface, delta, boundBox):
    """Rebuild a supported analytic splitting surface with a small displacement."""
    perturbed = surface.copy()

    if surface.type == "plane":
        normal, distance = surface.params
        perturbed.params = (normal, distance + delta)
    elif surface.type == "sphere":
        center, radius = surface.params
        if radius + delta <= 0:
            return None
        perturbed.params = (center, radius + delta)
    elif surface.type == "cylinder":
        point, axis, radius = surface.params
        if radius + delta <= 0:
            return None
        perturbed.params = (point, axis, radius + delta)
    elif surface.type == "torus":
        center, axis, major, minor_a, minor_b = surface.params
        if minor_a + delta <= 0 or minor_b + delta <= 0:
            return None
        perturbed.params = (center, axis, major, minor_a + delta, minor_b + delta)
    else:
        return None

    perturbed.buildShape(boundBox)
    if perturbed.shape is None or perturbed.shape.isNull():
        return None
    return perturbed.shape


def _splitWithRetry(base, surfaces, tolerance):
    """Split a base and retry failed results with bounded perturbations.

    Return None when lazy-mode retries are exhausted so the caller can discard
    the failed base instead of treating it as an unsplit result.
    """
    tools = tuple(surface.shape for surface in surfaces)
    try:
        result = BOPTools.SplitAPI.slice(base.shape, tools, "Split", tolerance=tolerance)
        originalParts = [TopoWrapper(solid) for solid in result.Solids]
    except Exception:
        originalParts = []

    if not originalParts:
        if base.status == "unchecked":
            base.check()
        if base.status != "healthy":
            repaired = base.repair()
            if repaired is None:
                return []

        try:
            result = BOPTools.SplitAPI.slice(base.shape, tools, "Split", tolerance=tolerance)
            originalParts = [TopoWrapper(solid) for solid in result.Solids]
        except Exception:
            return []

        if not originalParts:
            return []

    if Options.lazyTopologyChecks and originalParts:
        outputVolume = 0.0
        for part in originalParts:
            outputVolume += abs(part.shape.Volume)
        volumeError = abs(outputVolume - abs(base.shape.Volume))
        volumeLimit = max(1.0e-6, abs(base.shape.Volume) * 5.0e-5)
        if volumeError <= volumeLimit:
            return originalParts

    for part in originalParts:
        part.check()
    if not Options.lazyTopologyChecks and originalParts:
        allHealthy = True
        for part in originalParts:
            if part.status != "healthy":
                allHealthy = False
                break
        if allHealthy:
            return originalParts

    if base.status == "unchecked":
        base.check()
    if base.status != "healthy":
        repaired = base.repair()
        if repaired is None:
            return originalParts

    surfaceIds = ", ".join(f"{surface.id} ({surface.type})" for surface in surfaces)
    issues = list(dict.fromkeys(issue for part in originalParts for issue in part.issues))
    issueText = "; ".join(issues[:3]) if issues else "no usable fragments"
    print(f"Retrying unhealthy split on surface(s) {surfaceIds}: {issueText}")

    maxPerturbation = min(abs(tolerance), 1.0e-4)
    perturbations = tuple(value for value in (1.0e-6, 1.0e-5, 1.0e-4) if value <= maxPerturbation)
    if maxPerturbation > 0 and not perturbations:
        perturbations = (maxPerturbation,)

    volumeLimit = max(1.0e-6, abs(base.shape.Volume) * 5.0e-5)
    for magnitude in perturbations:
        bestCandidate = None
        for toolIndex, surface in enumerate(surfaces):
            for delta in (magnitude, -magnitude):
                tool = _perturbSurface(surface, delta, base.shape.BoundBox)
                if tool is None:
                    continue

                perturbedTools = list(tools)
                perturbedTools[toolIndex] = tool
                try:
                    result = BOPTools.SplitAPI.slice(base.shape, tuple(perturbedTools), "Split", tolerance=0)
                    parts = [TopoWrapper(solid) for solid in result.Solids]
                    for part in parts:
                        part.check()
                except Exception:
                    continue

                if len(parts) < 2 or any(part.status != "healthy" for part in parts):
                    continue

                volumeError = abs(sum(abs(part.shape.Volume) for part in parts) - abs(base.shape.Volume))
                if volumeError <= volumeLimit and (bestCandidate is None or volumeError < bestCandidate[0]):
                    bestCandidate = (volumeError, toolIndex, delta, parts)

        if bestCandidate is not None:
            volumeError, toolIndex, delta, parts = bestCandidate
            print(f"Split retry succeeded on surface {surfaces[toolIndex].id} with perturbation {delta:g}; volume error {volumeError:g}")
            return parts

    if Options.lazyTopologyChecks:
        print("Split retry failed; discarding the affected part")
        return None

    print("Split retry failed; keeping the original fragments for repair or discard")
    return originalParts


def joinBase(baseList):
    shape = []
    surf = {}
    removedKeys = []
    fwd = True
    for b in baseList:
        if b.orientation == "Reversed":
            fwd = False
        if b.base is not None:
            shape.append(b.base)
        for k, v in b.knownSurf.items():
            if k in removedKeys:
                continue
            if k not in surf.keys():
                surf[k] = v
            else:
                if surf[k] == v:
                    continue
                else:
                    surf[k] = None
                    removedKeys.append(k)

    newbase = FuseSolid(shape)
    orientation = "Forward" if fwd else "Reversed"
    return SplitBase(newbase, surf, orientation)


# TODO rename this function as there are two with the name name
def SplitSolid(base, surfacesCut, cellObj, tolerance=0.01):  # 1e-2
    """Split wrapped geometry while keeping unsafe compound children separate."""
    # split Base (shape Object or list/tuple of shapes)
    # with selected surfaces (list of surfaces objects) cutting the base(s) (surfacesCut)
    # cellObj is the CAD object of the working cell to reconstruction.
    # the function return a list of solids enclosed fully in the cell (fullPart)
    # and a list of solids not fully enclosed in the cell (cutPart). These lasts
    # will require more splitting with the others surfaces defining the cell.

    fullPart = []
    cutPart = []

    # part if several base in input

    if type(base) is list or type(base) is tuple:
        for b in base:
            fullList, cutList = SplitSolid(b, surfacesCut, cellObj, tolerance=tolerance)
            fullPart.extend(fullList)
            cutPart.extend(cutList)
        return fullPart, cutPart

    if base.base.shape.ShapeType == "Compound" and base.base.bop_safe is False:
        children = [SplitBase(part, base.knownSurf, base.orientation) for part in base.base.parts]
        return SplitSolid(children, surfacesCut, cellObj, tolerance)

    # part if base is shape object
    # resulting cell orientation is "Reversed" only if both
    # cells have reversed orientations
    if cellObj.boundBox.Orientation == base.orientation:
        orientation = cellObj.boundBox.Orientation
    else:
        orientation = "Forward"

    if abs(base.base.shape.Volume / base.base.shape.Area) < 1e-2:
        return fullPart, cutPart

    Tools = tuple(s.shape for s in surfacesCut)
    if Tools[0] is not None:
        splitParts = _splitWithRetry(base.base, surfacesCut, tolerance)
        if splitParts is None:
            return fullPart, cutPart
        if not splitParts and Options.lazyTopologyChecks and base.base.shape.ShapeType == "Compound":
            if base.base.status == "unchecked":
                base.base.check()
            if base.base.status != "healthy" or base.base.bop_safe is not True:
                base.base.repair(require_bop_safe=True)
            if base.base.parts:
                children = []
                for part in base.base.parts:
                    children.append(SplitBase(part, base.knownSurf, base.orientation))
                return SplitSolid(children, surfacesCut, cellObj, tolerance)
        if not splitParts:
            splitParts = [base.base]
    else:
        splitParts = [base.base]

    partPositions, partSolids = space_decomposition(splitParts, surfacesCut)

    for pos, wrapped in zip(partPositions, partSolids):
        sol = wrapped.shape
        # fullPos = updateSurfacesValues(pos,cellObj.surfaces,base.knownSurf)
        # inSolid = cellObj.definition.evaluate(fullPos)

        pos.update(base.knownSurf)
        inSolid = cellObj.definition.evaluate(pos)

        # if solidTool :
        #  ii += 1
        #  print(solidTool)
        #  print(cellObj.definition)
        #  print(pos)
        #  print('eval',inSolid)
        #  name = str(cellObj.definition)
        #  sol.exportStep('solid_{}{}.stp'.format(name,ii))

        if inSolid:
            if not Options.lazyTopologyChecks:
                if wrapped.status == "unchecked":
                    wrapped.check()
                if wrapped.status != "healthy":
                    repaired = wrapped.repair()
                    if repaired is None:
                        continue

            if wrapped.shape.ShapeType == "Compound":
                if wrapped.parts is None:
                    wrapped.parts = []
                    for solid in wrapped.shape.Solids:
                        wrapped.parts.append(TopoWrapper(solid))
                fullPart.extend(SplitBase(part, pos, orientation) for part in wrapped.parts)
            else:
                fullPart.append(SplitBase(wrapped, pos, orientation))
        elif inSolid is None:
            cutPart.append(SplitBase(wrapped, pos, orientation))
    return fullPart, cutPart


def updateSurfacesValues(position, surfaces, knownSurf):
    position.update(knownSurf)
    sname = set(surfaces.keys())
    pname = set(position.keys())

    fullpos = position.copy()
    for name in sname.difference(pname):
        fullpos[name] = None
    return fullpos


# Get the position of subregion with respect
# all cutting surfaces
def space_decomposition(parts, surfaces):
    """Classify wrappers relative to the original splitting surfaces."""

    component = []
    good_solids = []
    for part in parts:
        if not Options.lazyTopologyChecks:
            if part.status == "unchecked":
                part.check()
            if part.status != "healthy":
                repaired = part.repair()
                if repaired is None:
                    continue

        c = part.shape
        if c.Volume < 1e-3:
            if abs(c.Volume) < 1e-3:
                continue
            else:
                c.reverse()
                print("Negative solid Volume", c.Volume)
        classificationFailed = False
        Svalues = {}
        try:
            point = point_inside(c)
            if point is None:
                classificationFailed = True
            else:
                for surf in surfaces:
                    Svalues[surf.id] = surface_side(point, surf)
        except Exception:
            classificationFailed = True

        if classificationFailed and Options.lazyTopologyChecks:
            if part.status == "unchecked":
                part.check()
            if part.status != "healthy":
                repaired = part.repair()
                if repaired is None:
                    continue

            c = part.shape
            Svalues = {}
            try:
                point = point_inside(c)
                if point is None:
                    classificationFailed = True
                else:
                    classificationFailed = False
                    for surf in surfaces:
                        Svalues[surf.id] = surface_side(point, surf)
            except Exception:
                classificationFailed = True

        if classificationFailed:
            continue

        component.append(Svalues)
        good_solids.append(part)
    return component, good_solids


def point_inside(solid):

    point = solid.Solids[0].CenterOfMass
    if solid.isInside(point, 0.0, False):
        return point

    L = 0.5 * abs(solid.Volume) ** 0.33333
    for face in solid.Faces:
        u0, u1, v0, v1 = face.ParameterRange
        u = 0.5 * (u0 + u1)
        v = 0.5 * (v0 + v1)
        if face.isPartOfDomain(u, v):
            normal = -face.normalAt(u, v)
            pos = face.valueAt(u, v)
            d = L
            for i in range(12):
                d = d * 0.5
                point = pos + d * normal
                if solid.isInside(point, 0.0, False):
                    return point


# find one point inside a solid (region)
def point_inside_org(solid):

    cut_line = 32
    cut_box = 4

    # no poner boundbox, el punto puente caer en una superficie para geometria triangular
    point = solid.CenterOfMass
    if solid.isInside(point, 0.0, False):
        return point

    v1 = solid.Vertexes[0].Point
    for vi in range(len(solid.Vertexes) - 1, 0, -1):
        v2 = solid.Vertexes[vi].Point
        dv = (v2 - v1) * 0.5

        n = 1
        while True:
            for i in range(n):
                point = v1 + dv * (1 + 0.5 * i)
                if solid.isInside(point, 0.0, False):
                    return point
            n = n * 2
            dv = dv * 0.5
            if n > cut_line:
                break

    #      Box_Volume = BBox.XLength*BBox.YLength*BBox.ZLength
    #      if (solid.Volume < Box_Volume/ math.pow(16,nmax_cut)) :
    #           print('very small Solid Volume (solid volume, box volume): {},{}'.format(solid.Volume,Box_Volume))
    #           return None
    BBox = solid.optimalBoundingBox(False)
    box = [BBox.XMin, BBox.XMax, BBox.YMin, BBox.YMax, BBox.ZMin, BBox.ZMax]

    boxes, centers = divide_box(box)
    n = 0

    while True:
        for p in centers:
            pp = FreeCAD.Vector(p[0], p[1], p[2])
            if solid.isInside(pp, 0.0, False):
                return pp

        subbox = []
        centers = []
        for b in boxes:
            btab, ctab = divide_box(b)
            subbox.extend(btab)
            centers.extend(ctab)
        boxes = subbox
        n = n + 1

        if n == cut_box:
            print(f"Solid not found in bounding Box (Volume : {solid.Volume})")
            print("Valid Solid : ", solid.isValid())
            return None


# divide a box into 8 smaller boxes
def divide_box(Box):
    xmid = (Box[1] + Box[0]) * 0.5
    ymid = (Box[3] + Box[2]) * 0.5
    zmid = (Box[5] + Box[4]) * 0.5

    b1 = (Box[0], xmid, Box[2], ymid, Box[4], zmid)
    p1 = (0.5 * (Box[0] + xmid), 0.5 * (Box[2] + ymid), 0.5 * (Box[4] + zmid))

    b2 = (xmid, Box[1], Box[2], ymid, Box[4], zmid)
    p2 = (0.5 * (xmid + Box[1]), 0.5 * (Box[2] + ymid), 0.5 * (Box[4] + zmid))

    b3 = (Box[0], xmid, ymid, Box[3], Box[4], zmid)
    p3 = (0.5 * (Box[0] + xmid), 0.5 * (ymid + Box[3]), 0.5 * (Box[4] + zmid))

    b4 = (xmid, Box[1], ymid, Box[3], Box[4], zmid)
    p4 = (0.5 * (xmid + Box[1]), 0.5 * (ymid + Box[3]), 0.5 * (Box[4] + zmid))

    b5 = (Box[0], xmid, Box[2], ymid, zmid, Box[5])
    p5 = (0.5 * (Box[0] + xmid), 0.5 * (Box[2] + ymid), 0.5 * (zmid + Box[5]))

    b6 = (xmid, Box[1], Box[2], ymid, zmid, Box[5])
    p6 = (0.5 * (xmid + Box[1]), 0.5 * (Box[2] + ymid), 0.5 * (zmid + Box[5]))

    b7 = (Box[0], xmid, ymid, Box[3], zmid, Box[5])
    p7 = (0.5 * (Box[0] + xmid), 0.5 * (ymid + Box[3]), 0.5 * (zmid + Box[5]))

    b8 = (xmid, Box[1], ymid, Box[3], zmid, Box[5])
    p8 = (0.5 * (xmid + Box[1]), 0.5 * (ymid + Box[3]), 0.5 * (zmid + Box[5]))

    return [b1, b2, b3, b4, b5, b6, b7, b8], [p1, p2, p3, p4, p5, p6, p7, p8]


# check the position of the point with respect
# a surface
def surface_side(p, surf):
    if surf.type == "sphere":
        org, R = surf.params
        D = p - org
        inout = D.Length - R

    elif surf.type == "plane":
        normal, d = surf.params
        inout = p.dot(normal) - d

    elif surf.type == "cylinder":
        P, v, R = surf.params

        D = p - P
        if not surf.truncated:
            inout = D.cross(v).Length - R
        else:
            inCyl = D.cross(v).Length / v.Length - R  # <0 in cylinder
            inPln = btwPPlanes(p, P, v)  # <0  between planes

            if (inCyl < 0) and (inPln < 0):
                inout = -1  # inside the can
            else:
                inout = 1  # outside the can

    elif surf.type == "cone":
        if not surf.truncated:
            P, v, t, dblsht = surf.params
            X = p - P
            X.normalize()
            dprod = X.dot(v)
            dprod = max(-1, min(1, dprod))
            a = math.acos(dprod) if not dblsht else math.acos(abs(dprod))
            inout = a - math.atan(t)
        else:
            P, v, R1, R2 = surf.params
            apex = P + R1 / (R1 - R2) * v

            X = p - apex
            X.normalize()
            dprod = X.dot(-v) / v.Length  # -v because reverse axis. in MCNP TRC r1 > r2
            dprod = max(-1, min(1, dprod))
            a = math.acos(dprod)

            t = (R1 - R2) / v.Length
            inCone = a - math.atan(t)
            inPln = btwPPlanes(p, P, v)  # <0  between planes

            if (inCone < 0) and (inPln < 0):
                inout = -1  # inside the can
            else:
                inout = 1  # outside the can

    elif surf.type == "cone_elliptic":
        apex, axis, Ra, radii, rAxes, dblsht = surf.params

        r = p - apex
        X = r.dot(rAxes[1])
        Y = r.dot(rAxes[0])
        Z = r.dot(axis)
        if dblsht:
            Z = abs(Z)
        inout = (X / radii[1]) ** 2 + (Y / radii[0]) ** 2 - Z / Ra

    elif surf.type == "hyperboloid":
        center, axis, radii, rAxes, onesht = surf.params

        r = p - center
        rX = r.dot(rAxes[1])
        v = r - (rX * rAxes[1] + center)
        d = v.Length

        one = 1 if onesht else -1
        radical = (rX / radii[1]) ** 2 + one

        if radical > 0:
            Y = radii[0] * math.sqrt(radical)
            inout = d - Y
        else:
            inout = 1

    elif surf.type == "ellipsoid":
        center, axis, radii, rAxes = surf.params

        r = p - center
        rX = r.dot(axis)
        rY = r - (rX * axis + center)

        if axis.add(-rAxes[0]).Length < 1e-5:
            radX, radY = radii
        else:
            radY, radY = radii

        radical = 1 - (rX / radX) ** 2
        if radical > 0:
            Y = radY * math.sqrt(radical)
            inout = rY - Y
        else:
            inout = 1

    elif surf.type == "cylinder_elliptic":
        center, axis, radii, rAxes = surf.params

        r = p - center
        X = r.dot(rAxes[1])
        Y = r.dot(rAxes[0])
        inout = (X / radii[1]) ** 2 + (Y / radii[0]) ** 2 - 1

        if surf.truncated and inout < 0:
            inout = btwPPlanes(p, center, axis)  # <0  between planes

    elif surf.type == "cylinder_hyperbolic":
        center, axis, radii, rAxes = surf.params

        r = p - center
        X = r.dot(rAxes[1])
        Y = r.dot(rAxes[0])
        inout = (X / radii[1]) ** 2 - (Y / radii[0]) ** 2 - 1

    elif surf.type == "paraboloid":
        center, axis, focal = surf.params

        r = p - center
        X = r.dot(axis)
        if X < 0:
            inout = 1
        else:
            v = r - X * axis
            d = v.Length
            Y = math.sqrt(4 * focal * X)
            inout = d - Y

    elif surf.type == "torus":
        P, v, Ra, Rb, Rc = surf.params

        d = p - P
        z = d.dot(v)
        rz = d - z * v
        inout = (z / Rb) ** 2 + ((rz.Length - Ra) / Rc) ** 2 - 1

    elif surf.type == "box":
        P, v1, v2, v3 = surf.params
        for v in (v1, v2, v3):
            inout = btwPPlanes(p, P, v)  # <0  between planes
            if inout > 0:
                break

    else:
        print(f"surface type {surf[0]} not considered")
        return

    return inout > 0


def btwPPlanes(p, p0, v):

    p1 = p0 + v
    inP0 = v.dot(p - p0)  # >0 plane(base plane) side inside the cylinder
    inP1 = v.dot(p - p1)  # >0 plane(base plane) side inside the cylinder

    if (inP0 > 0) and (inP1 < 0):
        return -1
    else:
        return 1

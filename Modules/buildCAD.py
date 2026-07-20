import BOPTools.SplitAPI
from tqdm import tqdm
import FreeCAD

from .buildSolidCell import FuseSolid
from .Utils.booleanFunction import BoolSequence
from .Utils.boundBox import myBox


def interferencia(container, cell, mode="common"):

    if mode == "common":
        return cell.shape.common(container.shape)

    Base = cell.shape
    Tool = (container.shape,)

    solids = BOPTools.SplitAPI.slice(Base, Tool, "Split", tolerance=0).Solids
    cellParts = []
    for s in solids:
        if container.shape.isInside(s.CenterOfMass, 0.0, False):
            cellParts.append(s)

    if not cellParts:
        return cell.shape
    else:
        return FuseSolid(cellParts)


def AssignSurfaceToCell(UniverseCells, modelSurfaces):

    for Uid, uniCells in UniverseCells.items():
        for c in uniCells.values():
            c.setSurfaces(modelSurfaces)


def get_universe_containers(levels, Universes):
    Ucontainer = {}
    for lev in range(1, len(levels)):
        for U, name in levels[lev]:
            UFILL = Universes[U][name].FILL
            if UFILL in Ucontainer.keys():
                Ucontainer[UFILL].append((U, name, lev))
            else:
                Ucontainer[UFILL] = [(U, name, lev)]
    return Ucontainer


def get_container_box(ContainerCell):
    if ContainerCell.shape is not None:
        external_box = myBox(ContainerCell.shape.BoundBox, "Forward")
        if ContainerCell.CurrentTR:
            external_box.Box = external_box.Box.transformed(ContainerCell.CurrentTR.inverse())
    else:
        external_box = ContainerCell.externalBox
    return external_box


def get_pass_through_box(cell, parent_box):
    if cell.latticeBox is None:
        return parent_box

    if cell.TRFL and parent_box is not None and parent_box.Box is not None:
        parent_box = myBox(parent_box.Box.transformed(cell.TRFL.inverse()), parent_box.Orientation)

    box = FreeCAD.BoundBox(cell.latticeBox)
    if parent_box is not None and parent_box.Box is not None:
        if box.XLength <= 1e-12:
            box.XMin = parent_box.Box.XMin
            box.XMax = parent_box.Box.XMax
        if box.YLength <= 1e-12:
            box.YMin = parent_box.Box.YMin
            box.YMax = parent_box.Box.YMax
        if box.ZLength <= 1e-12:
            box.ZMin = parent_box.Box.ZMin
            box.ZMax = parent_box.Box.ZMax

    lattice_box = myBox(box, "Forward")
    if parent_box is not None:
        lattice_box.mult(parent_box)
    return lattice_box


def get_boundbox_enlarge(ContainerCell):
    if getattr(ContainerCell, "latticeBox", None) is not None:
        return 0
    return 0.2


def BuildUniverseCells(startInfo, ContainerCell, AllUniverses, universeCut=True):

    CADUniverse = []
    Ustart, levelMax = startInfo
    Universe = AllUniverses[Ustart]

    if ContainerCell.name is not None:
        print(f"Build Universe {ContainerCell.FILL} in container cell {ContainerCell.name}")
    else:
        print(f"Build Universe {ContainerCell.FILL}")
    fails = []
    for NTcell in tqdm(Universe.values(), desc="build cell"):

        if NTcell.definition is None:
            if not NTcell.FILL:
                fails.append(NTcell.name)
                continue

            cell = NTcell.copy()
            external_box = get_pass_through_box(cell, get_container_box(ContainerCell))

            cell.externalBox = external_box
            cell.boundBox = external_box

            if ContainerCell.level + 1 > levelMax:
                continue

            if ContainerCell.CurrentTR and cell.TRFL:
                cell.CurrentTR = ContainerCell.CurrentTR.multiply(cell.TRFL)
            elif ContainerCell.CurrentTR:
                cell.CurrentTR = ContainerCell.CurrentTR
            cell.level = ContainerCell.level + 1
            univ, ff = BuildUniverseCells((cell.FILL, levelMax), cell, AllUniverses, universeCut=universeCut)
            CADUniverse.append(univ)
            fails.extend(ff)
            continue

        cell = NTcell.copy()
        if type(cell.definition) is not BoolSequence:
            cell.definition = BoolSequence(cell.definition.str)

        external_box = get_container_box(ContainerCell)

        debug = True
        enlarge = get_boundbox_enlarge(ContainerCell)
        if debug:
            cell.build_BoundBox(external_box, enlarge=enlarge)
            if cell.boundBox.Orientation == "Forward" and cell.boundBox.Box is None:
                cell.shape = None
            else:
                if cell.boundBox.Orientation == "Forward":
                    cell.externalBox = cell.boundBox
                cell.buildShape(simplify=False)
        else:
            try:
                cell.build_BoundBox(external_box, enlarge=enlarge)
                if cell.boundBox.Orientation == "Forward" and cell.boundBox.Box is None:
                    cell.shape = None
                else:
                    if cell.boundBox.Orientation == "Forward":
                        cell.externalBox = cell.boundBox
                    cell.buildShape(simplify=False)
            except:
                fails.append(cell.name)

        if cell.shape is None:
            continue

        if ContainerCell.CurrentTR:
            cell.transformSolid(ContainerCell.CurrentTR)

        if universeCut and ContainerCell.shape:
            cell.shape = interferencia(ContainerCell, cell)

        if not cell.FILL or ContainerCell.level + 1 > levelMax:
            CADUniverse.append(cell)
        else:
            if ContainerCell.CurrentTR and cell.TRFL:
                cell.CurrentTR = ContainerCell.CurrentTR.multiply(cell.TRFL)
            elif ContainerCell.CurrentTR:
                cell.CurrentTR = ContainerCell.CurrentTR
            cell.level = ContainerCell.level + 1
            univ, ff = BuildUniverseCells((cell.FILL, levelMax), cell, AllUniverses, universeCut=universeCut)
            CADUniverse.append(univ)
            fails.extend(ff)

    return ((ContainerCell.name, Ustart), CADUniverse), fails


def makeTree(CADdoc, CADCells):

    label, universeCADCells = CADCells
    groupObj = CADdoc.addObject("App::Part", "Materials")

    groupObj.Label = f"Universe_{label[1]}_Container_{label[0]}"

    CADObj = {}
    for i, c in enumerate(universeCADCells):
        if isinstance(c, (tuple, list)):
            groupObj.addObject(makeTree(CADdoc, c))
        else:
            featObj = CADdoc.addObject("Part::FeaturePython", f"solid{i}")
            featObj.Label = f"Cell_{c.name}_{c.MAT}"
            featObj.Shape = c.shape
            if c.MAT not in CADObj.keys():
                CADObj[c.MAT] = [featObj]
            else:
                CADObj[c.MAT].append(featObj)

    for mat, matGroup in CADObj.items():
        groupMatObj = CADdoc.addObject("App::Part", "Materials")
        groupMatObj.Label = f"Material_{mat}_{label[0]}{label[1]}"
        groupMatObj.addObjects(matGroup)
        groupObj.addObject(groupMatObj)

    return groupObj


def iterLeafCells(CADCells):
    if isinstance(CADCells, tuple):
        for c in CADCells[1]:
            yield from iterLeafCells(c)
    elif isinstance(CADCells, list):
        for c in CADCells:
            yield from iterLeafCells(c)
    else:
        yield CADCells


def makeMaterialTree(CADdoc, CADCells):
    label = CADCells[0]
    groupObj = CADdoc.addObject("App::Part", "Materials")
    groupObj.Label = f"Universe_{label[1]}_Container_{label[0]}_Fused"

    materialShapes = {}
    for cell in iterLeafCells(CADCells):
        if cell.shape is None:
            continue
        if cell.MAT not in materialShapes:
            materialShapes[cell.MAT] = []
        materialShapes[cell.MAT].append(cell.shape)

    sorted_materials = sorted(materialShapes.items())
    material_count = len(sorted_materials)
    for index, (mat, shapes) in enumerate(sorted_materials, start=1):
        print(
            f"CAD export: fusing material {mat} "
            f"({index}/{material_count}) from {len(shapes)} shapes",
            flush=True,
        )
        featObj = CADdoc.addObject("Part::FeaturePython", f"material_{mat}")
        featObj.Label = f"Material_{mat}"
        featObj.Shape = FuseSolid(shapes)
        groupObj.addObject(featObj)
        print(
            f"CAD export: finished material {mat} "
            f"({index}/{material_count})",
            flush=True,
        )

    return groupObj

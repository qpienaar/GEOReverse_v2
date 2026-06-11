import FreeCAD

from .remh import Cline


class CellCard:

    def __init__(self, data):
        self.type = "cell"
        self.TR = None
        self.processData(data)

    def processData(self, data):
        self.name = int(data["id"])
        self.level = None

        if "material" in data.keys():
            self.MAT = 0 if data["material"] == "void" else int(data["material"])
        else:
            self.MAT = None

        if "universe" in data.keys():
            self.U = int(data["universe"])
        else:
            self.U = 0

        if "fill" in data.keys():
            self.FILL = int(data["fill"])
        else:
            self.FILL = None

        if "region" in data.keys():
            self.geom = Cline(data["region"].replace("|", ":"))
        elif self.FILL is not None:
            self.geom = None
        else:
            raise ValueError(f"Cell {self.name} has no region and does not fill a universe")


class CellIdAllocator:
    def __init__(self, used_ids):
        self.used_ids = set(used_ids)
        self.next_id = max(self.used_ids, default=0) + 1

    def get_id(self):
        while self.next_id in self.used_ids:
            self.next_id += 1
        new_id = self.next_id
        self.used_ids.add(new_id)
        self.next_id += 1
        return new_id


class LatticeCard:
    def __init__(self, card, cell_id_allocator):
        self.type = "lattice"
        self.name = int(card.attrib["id"])
        self.cell_id_allocator = cell_id_allocator
        self.processData(card)

    def processData(self, card):
        lower_left = _get_float_values(card, "lower_left")
        pitch = _get_float_values(card, "pitch")
        dimensions = _get_int_values(card, ("dimension", "dimensions"))
        universes = _get_int_values(card, "universes")

        if len(dimensions) not in (2, 3):
            raise ValueError(f"Lattice {self.name} dimension must have 2 or 3 values")

        if len(lower_left) != len(dimensions):
            raise ValueError(f"Lattice {self.name} lower_left does not match dimension length")

        if len(pitch) != len(dimensions):
            raise ValueError(f"Lattice {self.name} pitch does not match dimension length")

        expected_universes = 1
        for dim in dimensions:
            expected_universes *= dim

        if len(universes) != expected_universes:
            raise ValueError(
                f"Lattice {self.name} has {len(universes)} universes, expected {expected_universes}"
            )

        self.cells = []
        if len(dimensions) == 2:
            nx, ny = dimensions
            for j in range(ny):
                for i in range(nx):
                    pos = j * nx + i
                    self.cells.append(self.__makeCell__(universes[pos], lower_left, pitch, (i, j)))
        else:
            nx, ny, nz = dimensions
            for k in range(nz):
                for j in range(ny):
                    for i in range(nx):
                        pos = k * nx * ny + j * nx + i
                        self.cells.append(self.__makeCell__(universes[pos], lower_left, pitch, (i, j, k)))

    def __makeCell__(self, universe, lower_left, pitch, indices):
        cell = CellCard(
            {
                "id": str(self.cell_id_allocator.get_id()),
                "universe": str(self.name),
                "fill": str(universe),
            }
        )
        cell.U = self.name
        translation = [lower_left[i] + (indices[i] + 0.5) * pitch[i] for i in range(len(indices))]
        while len(translation) < 3:
            translation.append(0.0)
        cell.TR = _translation_matrix(translation)
        return cell


class SurfCard:
    def __init__(self, data):

        self.type = "surface"
        self.processData(data)

    def processData(self, data):
        self.name = int(data["id"])
        self.stype = data["type"]
        self.scoefs = tuple(float(x) for x in data["coeffs"].split())


def get_cards(root):
    cell_id_allocator = CellIdAllocator(_get_cell_ids(root))
    for c in root:
        cards = process_card(c, cell_id_allocator)
        if cards is None:
            continue
        if isinstance(cards, (list, tuple)):
            for card in cards:
                yield card
        else:
            yield cards


def process_card(card, cell_id_allocator=None):
    ctype = _tag(card)
    if ctype == "cell":
        return CellCard(card.attrib)

    elif ctype == "surface":
        return SurfCard(card.attrib)

    elif ctype == "lattice":
        if cell_id_allocator is None:
            cell_id_allocator = CellIdAllocator(())
        return LatticeCard(card, cell_id_allocator).cells


def _tag(card):
    return card.tag.rsplit("}", 1)[-1]


def _get_cell_ids(root):
    cell_ids = []
    for card in root:
        if _tag(card) == "cell" and "id" in card.attrib:
            cell_ids.append(int(card.attrib["id"]))
    return cell_ids


def _find_child(card, names):
    if isinstance(names, str):
        names = (names,)
    for child in card:
        if _tag(child) in names:
            return child
    raise ValueError(f"{_tag(card).capitalize()} {card.attrib.get('id')} has no {'/'.join(names)} field")


def _get_values(card, names):
    if isinstance(names, str):
        names = (names,)
    for name in names:
        if name in card.attrib:
            return card.attrib[name].split()
    child = _find_child(card, names)
    if child.text is None:
        return []
    return child.text.split()


def _get_float_values(card, name):
    return [float(value) for value in _get_values(card, name)]


def _get_int_values(card, name):
    return [int(value) for value in _get_values(card, name)]


def _translation_matrix(translation, scale=10.0):
    return FreeCAD.Matrix(
        1,
        0,
        0,
        translation[0] * scale,
        0,
        1,
        0,
        translation[1] * scale,
        0,
        0,
        1,
        translation[2] * scale,
        0,
        0,
        0,
        1,
    )

"""Utilities for producing valid solid unions from Open CASCADE shapes."""

import Part

from .diagnostics import report


def _result_solids(result):
    """Return valid solids from a valid Boolean result, or None."""
    if result is None or result.isNull() or not result.isValid():
        return None

    solids = list(result.Solids)
    if not solids or any(not solid.isValid() for solid in solids):
        return None
    return solids


def _boolean_union(solids):
    """Attempt one OCCT Boolean union without normalizing its shape type."""
    result = solids[0] if len(solids) == 1 else solids[0].fuse(solids[1:])
    if result.Volume < 0:
        result.reverse()
    return result


def _share_face(left, right):
    """Return whether two solids have a face-sized common boundary."""
    for left_face in left.Faces:
        for right_face in right.Faces:
            if left_face.common(right_face).Faces:
                return True
    return False


def _relationship(left, right):
    """Classify relationships that make a Boolean merge worth attempting."""
    common = left.common(right)
    if common.Solids:
        return "overlap"
    if _share_face(left, right):
        return "face"
    return None


def _adaptive_union(solids):
    """Merge compatible pairs while retaining non-manifold contacts."""
    working = list(solids)
    nonmergeable = set()

    while True:
        merged = False

        for left_index, left in enumerate(working):
            for right_index in range(left_index + 1, len(working)):
                pair = (left_index, right_index)
                if pair in nonmergeable:
                    continue

                right = working[right_index]
                relationship = _relationship(left, right)
                if relationship is None:
                    nonmergeable.add(pair)
                    continue

                result = _boolean_union([left, right])
                result_solids = _result_solids(result)
                if result_solids is not None and len(result_solids) == 1:
                    working = [
                        solid
                        for index, solid in enumerate(working)
                        if index not in pair
                    ]
                    working.extend(result_solids)
                    nonmergeable.clear()
                    merged = True
                    break

                if relationship == "overlap":
                    report(
                        "unresolved_volumetric_overlap",
                        [left, right],
                        result=result,
                        details=[
                            f"left_index={left_index}",
                            f"right_index={right_index}",
                        ],
                    )
                    raise RuntimeError(
                        "OCCT could not resolve a volumetric overlap between solids"
                    )

                # A shared-face merge is optional. If OCCT cannot create one
                # valid manifold solid, retain both valid solids separately.
                report(
                    "nonmanifold_shared_face_union_retained_separately",
                    [left, right],
                    result=result,
                    details=[
                        f"left_index={left_index}",
                        f"right_index={right_index}",
                    ],
                )
                nonmergeable.add(pair)

            if merged:
                break

        if not merged:
            return working


def FuseSolid(parts):
    """Return the Boolean union of every solid represented by ``parts``.

    A connected result is returned as a Solid. A disconnected result is
    returned as a Compound whose children are the separate Solid entities.
    """
    solids = []

    for part in parts:
        if part is None or part.isNull():
            continue

        part_solids = [part] if part.ShapeType == "Solid" else list(part.Solids)
        if not part_solids:
            report(
                "input_shape_contains_no_solids",
                [part],
                details=[f"part_index={len(solids)}"],
            )
            raise TypeError(
                f"Cannot fuse {part.ShapeType}: shape contains no solids"
            )
        invalid_solids = [solid for solid in part_solids if not solid.isValid()]
        if invalid_solids:
            report(
                "invalid_input_solid",
                list(parts),
                details=[
                    f"part_shape_type={part.ShapeType}",
                    f"invalid_solid_count={len(invalid_solids)}",
                ],
            )
            raise RuntimeError("Cannot fuse an invalid input solid")
        solids.extend(part_solids)

    if not solids:
        return None

    result = _boolean_union(solids)
    result_solids = _result_solids(result)
    if result_solids is None:
        report(
            "full_union_invalid_using_adaptive_fallback",
            solids,
            result=result,
            details=[f"input_solid_count={len(solids)}"],
        )
        result_solids = _adaptive_union(solids)

    if len(result_solids) == 1:
        return result_solids[0]

    compound = Part.makeCompound(result_solids)
    if not compound.isValid():
        report(
            "invalid_result_compound",
            result_solids,
            result=compound,
        )
        raise RuntimeError("OCCT produced an invalid compound of result solids")
    return compound

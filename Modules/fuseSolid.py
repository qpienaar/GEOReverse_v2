"""Utilities for producing valid solid unions from Open CASCADE shapes."""

import Part


def _result_solids(result):
    """Return valid solids from a valid Boolean result, or None."""
    if result is None or result.isNull() or not result.isValid():
        return None

    solids = list(result.Solids)
    if not solids or any(not solid.isValid() for solid in solids):
        return None
    return solids


def _boolean_union(solids):
    """Attempt one OCCT Boolean union and remove redundant split boundaries."""
    result = solids[0] if len(solids) == 1 else solids[0].fuse(solids[1:])

    if len(solids) > 1:
        try:
            refined = result.removeSplitter()
        except Exception:
            refined = None
        if refined is not None and not refined.isNull() and refined.isValid():
            result = refined

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
                    raise RuntimeError(
                        "OCCT could not resolve a volumetric overlap between solids"
                    )

                # A shared-face merge is optional. If OCCT cannot create one
                # valid manifold solid, retain both valid solids separately.
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
    parts = [part for part in parts if part is not None and not part.isNull()]

    if len(parts) <= 1:
        if parts:
            solid = parts[0]
        else:
            return None
    else:
        try:
            fused = parts[0].fuse(parts[1:])
        except Exception:
            fused = None

        if fused is not None:
            try:
                refined_fused = fused.removeSplitter()
            except Exception:
                refined_fused = fused

            if refined_fused.isValid():
                solid = refined_fused
            elif fused.isValid():
                solid = fused
            else:
                solid = Part.makeCompound(parts)
        else:
            solid = Part.makeCompound(parts)

    if solid.Volume < 0:
        solid.reverse()
    return solid

"""Utilities for producing valid solid unions and repairing topology."""

import Part

from .data_class import Options


def _boundingBoxGroups(parts):
    """Group parts by transitive intersection of existing bounding boxes."""
    if not parts:
        return []
    if len(parts) == 1:
        return [parts]

    parent = list(range(len(parts)))

    def find_root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def merge_groups(left_index, right_index):
        left_root = find_root(left_index)
        right_root = find_root(right_index)
        if left_root != right_root:
            parent[right_root] = left_root

    boxes = [part.shape.BoundBox for part in parts]
    for left_index, left_box in enumerate(boxes):
        for right_index in range(left_index + 1, len(boxes)):
            if left_box.intersect(boxes[right_index]):
                merge_groups(left_index, right_index)

    groups_by_root = {}
    root_order = []
    for index, part in enumerate(parts):
        root = find_root(index)
        if root not in groups_by_root:
            groups_by_root[root] = []
            root_order.append(root)
        groups_by_root[root].append(part)

    groups = []
    for root in root_order:
        groups.append(groups_by_root[root])
    return groups


class TopoWrapper:
    """Keep a TopoShape together with its cached topology-health result."""

    def __init__(self, shape, parts=None, status="unchecked", issues=None, bop_safe=None, contacts=None):
        """Store a shape together with its cached health and BOP-safety state."""
        self.shape = shape
        self.status = status
        self.issues = [] if issues is None else issues
        self.parts = parts
        self.bop_safe = bop_safe
        self.contacts = [] if contacts is None else contacts

    def replaceShape(self, shape, parts=None):
        """Replace the shape and invalidate all cached topology information."""
        self.shape = shape
        self.status = "unchecked"
        self.issues = []
        self.parts = parts
        self.bop_safe = None
        self.contacts = []

    def check(self, tolerance=1.0e-7):
        """Check an unchecked Solid or Compound and update its status."""
        if self.status != "unchecked":
            return

        if self.shape.ShapeType == "Compound":
            self._checkCompound(tolerance)
        else:
            self._checkSolid()

    def repair(self, tolerance=1.0e-4, require_bop_safe=False):
        """Repair topology and optionally force a compound to become BOP-safe."""
        if Options.lazyTopologyChecks and self.shape.ShapeType == "Compound" and self.status != "healthy":
            # Avoid _checkCompound()'s all-pairs scan in the lazy path.
            return self._repairCompound(tolerance, require_bop_safe)

        if self.status == "unchecked":
            self.check()

        if self.shape.ShapeType != "Compound":
            if self.status == "healthy":
                return self.shape
            return self._repairSolid()

        repairable_contacts = ("overlap", "face", "near", "unknown")
        needs_contact_repair = any(contact[2] in repairable_contacts for contact in self.contacts)
        if self.status == "healthy" and require_bop_safe and self.bop_safe:
            return self.shape
        if self.status == "healthy" and not needs_contact_repair and not require_bop_safe:
            return self.shape

        return self._repairCompound(tolerance, require_bop_safe)

    def _checkSolid(self):
        """Check the intrinsic topology health of a non-compound shape."""
        if not self.shape.isValid():
            self.issues.append("solid.isValid() error")

        if self.shape.Volume < 0:
            self.issues.append("Negative volume")

        try:
            self.shape.check(True)
        except Exception as error:
            ignored = ("No error", "BOP check found the following errors:")
            self.issues.extend(line.strip() for line in str(error).splitlines() if line.strip() and line.strip() not in ignored)

        self.issues = list(dict.fromkeys(self.issues))
        self.status = "healthy" if not self.issues else "unhealthy"
        self.bop_safe = self.status == "healthy"

    def _checkCompound(self, tolerance):
        """Check child health and classify the contacts within a compound."""
        if self.parts is None:
            self.parts = [TopoWrapper(solid) for solid in self.shape.Solids]

        if not self.parts:
            self.issues.append("Empty compound")
        else:
            for part in self.parts:
                if part.status == "unchecked":
                    part.check(tolerance)
                if part.status == "unhealthy":
                    self.issues.extend(part.issues)

        solid_face_count = sum(len(part.shape.Faces) for part in self.parts or [])
        if len(self.shape.Faces) != solid_face_count:
            self.issues.append("Compound contains non-solid topology")

        self.contacts = []
        for left_index, left in enumerate(self.parts or []):
            left_box = left.shape.BoundBox
            for right_index in range(left_index + 1, len(self.parts)):
                right = self.parts[right_index]
                if not left_box.intersect(right.shape.BoundBox):
                    continue

                contact = self._classifyContact(left, right, tolerance)
                if contact is not None:
                    self.contacts.append((left_index, right_index, contact[0], contact[1]))

        self.issues = list(dict.fromkeys(self.issues))
        self.status = "healthy" if not self.issues else "unhealthy"
        unsafe_contacts = ("overlap", "face", "edge", "vertex", "unknown")
        self.bop_safe = self.status == "healthy" and not any(contact[2] in unsafe_contacts for contact in self.contacts)

    def _classifyContact(self, left, right, contact_tolerance=1.0e-7, near_tolerance=1.0e-4):
        """Classify the geometric relationship between two healthy solids."""
        try:
            distance = left.shape.distToShape(right.shape)[0]
        except Exception:
            return "unknown", 0.0

        if distance > near_tolerance:
            return None

        try:
            common = left.shape.common(right.shape)
            volume_limit = max(1.0e-9, min(abs(left.shape.Volume), abs(right.shape.Volume)) * 1.0e-12)
            if abs(common.Volume) > volume_limit:
                return "overlap", distance
        except Exception:
            return "unknown", distance

        if distance > contact_tolerance:
            return "near", distance

        try:
            face_area = max((left_face.common(right_face).Area for left_face in left.shape.Faces for right_face in right.shape.Faces), default=0.0)
            if face_area > max(1.0e-10, contact_tolerance * contact_tolerance):
                return "face", distance
        except Exception:
            return "unknown", distance

        try:
            section = left.shape.section(right.shape)
            if section.Length > contact_tolerance:
                return "edge", distance
            if section.Vertexes:
                return "vertex", distance
        except Exception:
            return "unknown", distance

        return "unknown", distance

    def _repairSolid(self):
        """Try solid repairs in increasing order of intervention."""
        if self.shape.Volume < 0:
            candidate = self.shape.copy()
            candidate.reverse()
            if self._acceptRepair(candidate):
                return self.shape

        issue_text = "\n".join(self.issues).lower()

        if "redundant" in issue_text:
            try:
                if self._acceptRepair(self.shape.removeSplitter()):
                    return self.shape
            except Exception:
                pass

        orientation_errors = ("unorientable shape", "bad orientation", "bad orientation of sub-shape")
        if any(issue in issue_text for issue in orientation_errors):
            try:
                fixer = Part.ShapeFix.Solid()
                fixer.init(self.shape)
                fixer.Precision = 1.0e-2
                fixer.MinTolerance = 1.0e-7
                fixer.MaxTolerance = 5.0e-2
                fixer.FixShellMode = True
                fixer.FixShellOrientationMode = True
                fixer.CreateOpenSolidMode = False
                fixer.perform()
                if self._acceptRepair(fixer.shape()):
                    return self.shape
            except Exception:
                pass

        if any("bopalgo" not in issue.lower() for issue in self.issues):
            try:
                fixer = Part.ShapeFix.Shape(self.shape)
                fixer.Precision = 1.0e-2
                fixer.MinTolerance = 1.0e-7
                fixer.MaxTolerance = 5.0e-2
                fixer.FixSameParameterMode = True
                fixer.FixVertexPositionMode = False
                fixer.FixVertexTolMode = True
                fixer.perform()
                if self._acceptRepair(fixer.shape()):
                    return self.shape
            except Exception:
                pass

        return None

    def _repairCompound(self, tolerance, require_bop_safe=False):
        """Repair connected child groups and preserve disconnected parts."""
        if self.parts is None:
            self.parts = [TopoWrapper(solid) for solid in self.shape.Solids]

        healthy_parts = []
        for part in self.parts:
            if part.status == "unchecked":
                part.check()

            if part.status != "healthy":
                if Options.lazyTopologyChecks:
                    print("Discarding unhealthy compound part in lazy mode:", "; ".join(part.issues))
                    continue

                if part.repair() is None:
                    print("Discarding unhealthy compound part:", "; ".join(part.issues))
                    continue

            if part.shape.ShapeType == "Compound":
                healthy_parts.extend(part.parts)
            else:
                healthy_parts.append(part)

        if not healthy_parts:
            return None

        groups = _boundingBoxGroups(healthy_parts)
        repaired_parts = []
        unresolved_lazy_fuse = False

        for group in groups:
            group_result = self._repairCompoundParts(group, tolerance, require_bop_safe)
            if not group_result:
                continue
            if Options.lazyTopologyChecks and len(group_result) > 1:
                unresolved_lazy_fuse = True
            repaired_parts.extend(group_result)

        if not repaired_parts:
            return None

        if len(repaired_parts) == 1:
            result = repaired_parts[0]
        elif Options.lazyTopologyChecks:
            result_bop_safe = True
            if unresolved_lazy_fuse:
                result_bop_safe = False
            for part in repaired_parts:
                if part.bop_safe is not True:
                    result_bop_safe = False
                    break
            result_shape = Part.makeCompound([part.shape for part in repaired_parts])
            result = TopoWrapper(result_shape, repaired_parts, "healthy", [], result_bop_safe, [])
        else:
            result = TopoWrapper(Part.makeCompound([part.shape for part in repaired_parts]), repaired_parts)
            result.check()

        if result.status == "unchecked":
            result.check()
        if result.status == "unhealthy":
            self.issues.extend(result.issues)
            return None
        if require_bop_safe and not result.bop_safe:
            return None

        self.shape = result.shape
        self.status = result.status
        self.issues = result.issues
        self.parts = result.parts
        self.bop_safe = result.bop_safe
        self.contacts = result.contacts
        return self.shape

    def _fuzzyFusePair(self, left, right, maximum_tolerance):
        """Try bounded fuzzy generalFuse tolerances on one failed pair."""
        common_volume = 0.0
        try:
            common_volume = abs(left.shape.common(right.shape).Volume)
        except Exception:
            pass

        expected_volume = abs(left.shape.Volume) + abs(right.shape.Volume) - common_volume
        for fuzzy in (1.0e-7, 1.0e-6, 1.0e-5, 1.0e-4):
            if fuzzy > maximum_tolerance:
                continue

            try:
                fragments, _fragment_map = left.shape.generalFuse([right.shape], fuzzy)
                candidate = Part.makeCompound(fragments.Solids).removeSplitter()
                if candidate.ShapeType == "Compound" and len(candidate.Solids) == 1:
                    candidate = candidate.Solids[0]
                wrapped = TopoWrapper(candidate)
                wrapped.check()
            except Exception:
                continue

            volume_limit = max(1.0e-6, expected_volume * 1.0e-9)
            volume_preserved = abs(abs(wrapped.shape.Volume) - expected_volume) <= volume_limit
            if wrapped.status == "healthy" and wrapped.shape.ShapeType == "Solid" and volume_preserved:
                return wrapped

        return None

    def _trimContact(self, left, right, contact_type, gap):
        """Create a local gap by trimming the smaller member of a tangent pair."""
        target = left if abs(left.shape.Volume) <= abs(right.shape.Volume) else right
        other = right if target is left else left

        scales = (1.0, 10.0, 100.0) if contact_type == "edge" else (1.0,)
        for scale in scales:
            try:
                section = target.shape.section(other.shape)
                if contact_type == "vertex":
                    tools = [Part.makeSphere(gap, vertex.Point) for vertex in section.Vertexes]
                elif contact_type == "edge":
                    tools = []
                    for edge in section.Edges:
                        radius = gap * scale
                        curve = edge.Curve
                        if edge.isClosed() and hasattr(curve, "Radius"):
                            tools.append(Part.makeTorus(curve.Radius, radius, curve.Center, curve.Axis))
                        else:
                            point = edge.valueAt(edge.FirstParameter)
                            tangent = edge.tangentAt(edge.FirstParameter)
                            profile = Part.Wire([Part.makeCircle(radius, point, tangent)])
                            tools.append(Part.Wire([edge]).makePipeShell([profile], True, False))
                elif contact_type == "face":
                    face_common = max((target_face.common(other_face) for target_face in target.shape.Faces for other_face in other.shape.Faces), key=lambda shape: shape.Area)
                    face = face_common.Faces[0]
                    u0, u1, v0, v1 = face.ParameterRange
                    normal = face.normalAt(0.5 * (u0 + u1), 0.5 * (v0 + v1))
                    tools = [face.extrude(normal * gap), face.extrude(normal * -gap)]
                else:
                    return None

                if not tools:
                    return None

                tool = tools[0] if len(tools) == 1 else tools[0].fuse(tools[1:])
                candidate = target.shape.cut(tool)
                if candidate.ShapeType == "Compound" and len(candidate.Solids) == 1:
                    candidate = candidate.Solids[0]
                wrapped = TopoWrapper(candidate)
                wrapped.check()
                distance = wrapped.shape.distToShape(other.shape)[0]
            except Exception:
                continue

            volume_loss = abs(target.shape.Volume) - abs(wrapped.shape.Volume)
            volume_limit = max(1.0e-6, abs(target.shape.Volume) * 1.0e-3)
            if wrapped.status == "healthy" and volume_loss <= volume_limit and distance > 1.0e-7:
                return (wrapped, right) if target is left else (left, wrapped)

        return None

    def _repairCompoundParts(self, parts, tolerance, require_bop_safe=False):
        """Process one bounding-box-connected group into usable parts."""
        if len(parts) < 2:
            return parts
        parts = parts.copy()

        if Options.lazyTopologyChecks:
            aggregate = parts[0]
            residual_parts = []

            for part in parts[1:]:
                if not aggregate.shape.BoundBox.intersect(part.shape.BoundBox):
                    residual_parts.append(part)
                    continue

                input_volume = abs(aggregate.shape.Volume) + abs(part.shape.Volume)
                try:
                    fused_shape = aggregate.shape.fuse(part.shape)
                except Exception:
                    residual_parts.append(part)
                    continue

                if fused_shape is None or fused_shape.isNull():
                    residual_parts.append(part)
                    continue
                if not _usableUnion(fused_shape, input_volume):
                    residual_parts.append(part)
                    continue
                if fused_shape.ShapeType == "Compound" and len(fused_shape.Solids) == 1:
                    fused_shape = fused_shape.Solids[0]
                if fused_shape.ShapeType != "Solid":
                    residual_parts.append(part)
                    continue

                fused_wrapper = TopoWrapper(fused_shape)
                fused_wrapper.check()
                if fused_wrapper.status != "healthy":
                    residual_parts.append(part)
                    continue

                # fuse() returned a new union containing aggregate and part.
                aggregate = fused_wrapper

            return [aggregate] + residual_parts

        contact_tolerance = 1.0e-7

        while len(parts) > 1:
            changed = False
            unresolved_pairs = []
            for left_index, left in enumerate(parts):
                left_box = left.shape.BoundBox
                for right_index in range(left_index + 1, len(parts)):
                    right = parts[right_index]
                    if not left_box.intersect(right.shape.BoundBox):
                        continue

                    contact = self._classifyContact(left, right, contact_tolerance, tolerance)
                    if contact is None:
                        continue
                    contact_type = contact[0]

                    candidate = None
                    try:
                        fused = left.shape.fuse(right.shape)
                        if fused is not None and not fused.isNull():
                            if fused.ShapeType == "Compound" and len(fused.Solids) == 1:
                                fused = fused.Solids[0]
                            checked = TopoWrapper(fused)
                            checked.check()
                            if checked.status == "healthy" and checked.shape.ShapeType == "Solid":
                                candidate = checked
                    except Exception as error:
                        pair_issues = [str(error)]
                    else:
                        pair_issues = [] if candidate is not None else ["Pair fuse did not produce a healthy solid"]

                    if candidate is None and contact_type in ("face", "near"):
                        candidate = self._fuzzyFusePair(left, right, tolerance)

                    if candidate is not None:
                        parts[left_index] = candidate
                        del parts[right_index]
                        changed = True
                        break

                    if contact_type in ("edge", "vertex", "face"):
                        if require_bop_safe:
                            trimmed = self._trimContact(left, right, contact_type, tolerance)
                            if trimmed is not None:
                                parts[left_index], parts[right_index] = trimmed
                                changed = True
                                break
                        continue

                    if contact_type == "near":
                        continue

                    candidate = self._fuzzyFusePair(left, right, tolerance)
                    if candidate is not None:
                        parts[left_index] = candidate
                        del parts[right_index]
                        changed = True
                        break

                    unresolved_pairs.append((left_index, right_index, pair_issues or [f"Unresolved {contact_type} contact"]))
                if changed:
                    break

            if changed:
                continue
            if not unresolved_pairs:
                break

            failure_count = {}
            for left_index, right_index, pair_issues in unresolved_pairs:
                failure_count[left_index] = failure_count.get(left_index, 0) + 1
                failure_count[right_index] = failure_count.get(right_index, 0) + 1
            discard_index = max(failure_count, key=lambda index: (failure_count[index], -abs(parts[index].shape.Volume)))
            discard_issues = [issue for left_index, right_index, pair_issues in unresolved_pairs if discard_index in (left_index, right_index) for issue in pair_issues]
            print("Discarding part after failed compound repair:", "; ".join(dict.fromkeys(discard_issues)))
            del parts[discard_index]

        return parts

    def _acceptRepair(self, candidate):
        """Accept a volume-preserving candidate after type-aware validation."""
        if candidate is None or candidate.isNull():
            return False

        volume_limit = max(1.0e-6, abs(self.shape.Volume) * 1.0e-9)
        if abs(abs(candidate.Volume) - abs(self.shape.Volume)) > volume_limit:
            return False

        result = TopoWrapper(candidate)
        if result.status == "unchecked":
            result.check()
        if result.status == "unhealthy":
            return False

        self.shape = result.shape
        self.status = result.status
        self.issues = result.issues
        self.parts = result.parts
        self.bop_safe = result.bop_safe
        self.contacts = result.contacts
        return True


def _usableUnion(shape, input_volume):
    """Return whether a union has usable structure and a possible volume."""
    if shape is None:
        return False
    if shape.isNull():
        return False
    if not shape.Solids:
        return False

    volume_limit = max(1.0e-6, input_volume * 1.0e-9)
    return abs(shape.Volume) <= input_volume + volume_limit


def _usableWrappedUnion(result, input_volume):
    """Accept a healthy lazy compound without using its overlap-counted volume."""
    if result is None or result.status != "healthy":
        return False
    if Options.lazyTopologyChecks and result.shape.ShapeType == "Compound" and result.parts is not None:
        return True
    return _usableUnion(result.shape, input_volume)


def FuseSolid(parts):
    """Fuse shapes or wrappers and return a TopoWrapper, or None."""
    parts = [part for part in parts if part is not None]
    parts = [part if isinstance(part, TopoWrapper) else TopoWrapper(part) for part in parts]
    parts = [part for part in parts if part.shape is not None and not part.shape.isNull()]

    if len(parts) == 1:
        part = parts[0]
        if Options.lazyTopologyChecks:
            if part.shape.ShapeType == "Compound" and part.parts is not None and len(part.parts) == 1:
                return part.parts[0]
            return part

        if part.status == "unchecked":
            part.check()
        if part.status != "healthy" and part.repair() is None:
            print("Discarding unhealthy part:", "; ".join(part.issues))
            return None
        if part.shape.ShapeType == "Compound" and len(part.parts) == 1:
            return part.parts[0]
        return part

    input_volume = 0.0
    for part in parts:
        input_volume += abs(part.shape.Volume)

    if len(parts) > 1 and Options.lazyTopologyChecks:
        try:
            other_shapes = []
            for part in parts[1:]:
                other_shapes.append(part.shape)

            fused_shape = parts[0].shape.fuse(other_shapes)
            if _usableUnion(fused_shape, input_volume):
                if fused_shape.ShapeType == "Compound" and len(fused_shape.Solids) == 1:
                    fused_shape = fused_shape.Solids[0]
                return TopoWrapper(fused_shape)
        except Exception:
            pass

    expanded = []
    for part in parts:
        if part.shape.ShapeType != "Compound":
            expanded.append(part)
            continue

        if Options.lazyTopologyChecks:
            if part.parts is None:
                part.parts = [TopoWrapper(solid) for solid in part.shape.Solids]
            expanded.extend(part.parts)
            continue

        if part.status == "unchecked":
            part.check()
        if part.status == "healthy":
            expanded.extend(part.parts)
        elif part.repair() is not None:
            expanded.extend(part.parts or [part])

    parts = expanded
    if not parts:
        print("No valid parts")
        return None

    healthy_parts = []
    for part in parts:
        if part.status == "unchecked":
            part.check()

        if part.status != "healthy":
            if Options.lazyTopologyChecks:
                print("Discarding unhealthy part in lazy mode:", "; ".join(part.issues))
                continue

            if part.repair() is None:
                print("Discarding unhealthy part:", "; ".join(part.issues))
                continue

        healthy_parts.append(part)

    if not healthy_parts:
        print("No healthy parts")
        return None
    if len(healthy_parts) == 1:
        return healthy_parts[0]

    try:
        result = TopoWrapper(healthy_parts[0].shape.fuse([part.shape for part in healthy_parts[1:]]))
    except Exception as error:
        result = TopoWrapper(Part.makeCompound([part.shape for part in healthy_parts]), healthy_parts, "unhealthy", [str(error)])
        result.repair()
        if _usableWrappedUnion(result, input_volume):
            return result
        return None

    if result.shape is None or result.shape.isNull():
        result = TopoWrapper(Part.makeCompound([part.shape for part in healthy_parts]), healthy_parts, "unhealthy", ["Null fuse result"])
        result.repair()
        if _usableWrappedUnion(result, input_volume):
            return result
        return None

    if result.shape.ShapeType == "Compound" and len(result.shape.Solids) == 1:
        result = TopoWrapper(result.shape.Solids[0])

    if result.status == "unchecked":
        if not (Options.lazyTopologyChecks and result.shape.ShapeType == "Compound"):
            result.check()
    repairable_contacts = ("overlap", "face", "near", "unknown")
    if result.status == "healthy" and any(contact[2] in repairable_contacts for contact in result.contacts):
        if result.repair() is not None and _usableWrappedUnion(result, input_volume):
            return result

    if _usableWrappedUnion(result, input_volume):
        return result

    if result.repair() is not None and _usableWrappedUnion(result, input_volume):
        return result

    result = TopoWrapper(Part.makeCompound([part.shape for part in healthy_parts]), healthy_parts, "unhealthy", ["Fuse did not produce a healthy union"])
    result.repair()
    if _usableWrappedUnion(result, input_volume):
        return result
    return None

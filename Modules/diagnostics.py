"""Opt-in diagnostics for GEOReverse topology and Boolean failures."""

from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count
import os
from pathlib import Path


_TRUE_VALUES = {"1", "true", "yes", "on"}
_current_cell = ContextVar("geouned_diagnostic_cell", default=None)
_event_numbers = count(1)
_announced = False


def enabled():
    """Return whether GEOUNED diagnostic output is enabled."""
    return os.getenv("GEOUNED_DIAGNOSTICS", "").strip().lower() in _TRUE_VALUES


def output_directory():
    """Return the directory used for diagnostic artifacts."""
    configured = os.getenv("GEOUNED_DIAGNOSTIC_DIR", "geouned_diagnostics")
    return Path(configured).expanduser().resolve()


def announce():
    """Print the diagnostic configuration once per process."""
    global _announced
    if not enabled() or _announced:
        return
    _announced = True
    print(
        f"[GEOUNED DIAGNOSTIC] enabled; artifacts={output_directory()}",
        flush=True,
    )


@contextmanager
def cell_context(cell):
    """Make a CadCell available to lower-level diagnostic reporters."""
    if not enabled():
        yield
        return

    announce()
    token = _current_cell.set(cell)
    try:
        yield
    finally:
        _current_cell.reset(token)


def _safe_value(callback, fallback="unavailable"):
    try:
        return callback()
    except Exception as error:
        return f"{fallback} ({type(error).__name__}: {error})"


def _cell_lines():
    cell = _current_cell.get()
    if cell is None:
        return ["cell_id=None"]

    definition = _safe_value(lambda: str(cell.definition))
    return [
        f"cell_id={getattr(cell, 'name', None)}",
        f"material={getattr(cell, 'MAT', None)}",
        f"universe={getattr(cell, 'U', None)}",
        f"fill={getattr(cell, 'FILL', None)}",
        f"level={getattr(cell, 'level', None)}",
        f"definition={definition}",
    ]


def _shape_lines(label, shape):
    if shape is None:
        return [f"{label}: None"]

    lines = [
        f"{label}.shape_type={_safe_value(lambda: shape.ShapeType)}",
        f"{label}.is_null={_safe_value(lambda: shape.isNull())}",
        f"{label}.is_valid={_safe_value(lambda: shape.isValid())}",
        f"{label}.orientation={_safe_value(lambda: shape.Orientation)}",
        f"{label}.solid_count={_safe_value(lambda: len(shape.Solids))}",
        f"{label}.shell_count={_safe_value(lambda: len(shape.Shells))}",
        f"{label}.face_count={_safe_value(lambda: len(shape.Faces))}",
        f"{label}.edge_count={_safe_value(lambda: len(shape.Edges))}",
        f"{label}.vertex_count={_safe_value(lambda: len(shape.Vertexes))}",
        f"{label}.volume={_safe_value(lambda: shape.Volume)}",
        f"{label}.area={_safe_value(lambda: shape.Area)}",
    ]

    box = _safe_value(lambda: shape.BoundBox, fallback=None)
    if hasattr(box, "XMin"):
        lines.append(
            f"{label}.bounds="
            f"({box.XMin}, {box.YMin}, {box.ZMin}) -> "
            f"({box.XMax}, {box.YMax}, {box.ZMax})"
        )

    try:
        check_result = shape.check(True)
    except Exception as error:
        check_result = f"{type(error).__name__}: {error}"
    lines.append(f"{label}.bop_check={check_result}")
    return lines


def report(event, shapes=(), result=None, details=(), severity="ERROR"):
    """Print and persist a diagnostic event and its associated shapes."""
    if not enabled():
        return None

    announce()
    event_number = next(_event_numbers)
    cell = _current_cell.get()
    cell_id = getattr(cell, "name", "unknown") if cell is not None else "unknown"
    event_name = f"event_{event_number:05d}_cell_{cell_id}_{event}"
    event_dir = output_directory() / event_name
    event_dir.mkdir(parents=True, exist_ok=True)

    severity = severity.upper()
    lines = [
        f"severity={severity}",
        f"event={event}",
        f"artifact_directory={event_dir}",
    ]
    lines.extend(_cell_lines())
    lines.extend(str(detail) for detail in details)

    for index, shape in enumerate(shapes):
        label = f"input_{index:03d}"
        lines.extend(_shape_lines(label, shape))
        if shape is not None:
            try:
                shape.exportBrep(str(event_dir / f"{label}.brep"))
            except Exception as error:
                lines.append(f"{label}.export_error={type(error).__name__}: {error}")

    if result is not None:
        lines.extend(_shape_lines("result", result))
        try:
            result.exportBrep(str(event_dir / "result.brep"))
        except Exception as error:
            lines.append(f"result.export_error={type(error).__name__}: {error}")

    manifest = "\n".join(lines) + "\n"
    (event_dir / "diagnostic.txt").write_text(manifest, encoding="utf-8")
    for line in lines:
        print(f"[GEOUNED DIAGNOSTIC {severity}] {line}", flush=True)
    return event_dir

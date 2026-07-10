# GEOReverse BREP Topology From CSG Surfaces

GEOReverse does not derive BREP topology by analytically solving every CSG
surface intersection itself. Instead, it converts CSG surfaces into FreeCAD
shape objects, asks FreeCAD/OpenCASCADE to split a bounded stock volume into
topological fragments, and then uses the original CSG Boolean expression to
decide which fragments belong to the cell.

## Core Idea

The algorithm follows the qualitative description from the GEOUNED/GEOReverse
paper:

1. Build a box that covers the CSG cell.
2. Convert all surfaces used by the cell definition into CAD cutting tools.
3. Split the box with those surfaces.
4. For each resulting region, determine the Boolean side of each relevant
   surface.
5. Evaluate the original CSG cell expression with those Boolean values.
6. Keep regions for which the expression is true.
7. Fuse the kept regions into the final CAD solid.

In other words, the CSG expression is not directly translated into a single
chain of CAD booleans. GEOReverse first partitions space, then classifies the
partition pieces.

## Main Pipeline

### 1. Parse CSG Input

`CsgToCad.read_csg_file()` reads either MCNP or OpenMC XML input:

- MCNP input is handled by `GEOReverse/Modules/MCNPinput.py`.
- OpenMC XML input is handled by `GEOReverse/Modules/XMLinput.py`.

The parser extracts:

- surface definitions,
- cell region expressions,
- material ids,
- universe and fill relationships,
- transforms.

The public entry point is `GEOReverse/core.py:CsgToCad`.

### 2. Store Cells as Boolean Expressions

Each geometry-bearing cell becomes a `CadCell`.

The cell definition is represented by `BoolSequence`, where each surface id is
a Boolean variable. A signed surface term is handled by inverting the Boolean
value when needed.

For example, after a fragment has been classified with respect to surface `10`,
the expression evaluator receives a mapping like:

```python
{10: True, 20: False, 30: True}
```

`BoolSequence.evaluate(...)` then determines whether the fragment belongs to
the CSG cell.

Relevant files:

- `GEOReverse/Modules/Objects.py`
- `GEOReverse/Modules/Utils/booleanFunction.py`

### 3. Build a Finite Stock Box

CSG cells may be mathematically unbounded, but a CAD BREP solid must be finite.
GEOReverse therefore computes a working bounding box for the cell.

`CadCell.build_BoundBox(...)` computes the effective box, and
`CadCell.makeBox()` creates the initial FreeCAD solid with `Part.makeBox(...)`.

This box is the initial stock volume that will be partitioned.

Relevant file:

- `GEOReverse/Modules/Objects.py`

### 4. Convert CSG Surfaces Into CAD Cutting Tools

Each parsed analytic CSG surface has a `buildShape(...)` method that creates a
FreeCAD/OpenCASCADE shape suitable for splitting.

Examples:

- planes become bounded polygonal `Part.Face` objects,
- spheres become `Part.makeSphere(...)`,
- cylinders become `Part.makeCylinder(...)`,
- cones become `Part.makeCone(...)`,
- toroidal and quadric-like surfaces use corresponding helper construction
  routines.

These cutting shapes are not the final cell solids. They are tools used to
partition the stock box.

Relevant file:

- `GEOReverse/Modules/Objects.py`

### 5. Split the Box

`BuildSolid(cell)` delegates to `BuildDepth(...)` and `BuildSolidParts(...)`.

`BuildSolidParts(...)` builds surface shapes for the current bounding box and
then splits the stock solid:

- planes are applied first,
- non-planar surfaces are applied afterward.

The actual split is performed in `SplitSolid(...)`:

```python
BOPTools.SplitAPI.slice(base.base, Tools, "Split", tolerance=tolerance).Solids
```

This is the key topology-building step. `BOPTools.SplitAPI.slice` is a FreeCAD
wrapper around OpenCASCADE boolean/splitting operations. OpenCASCADE computes
the intersections, creates BREP vertices, edges, faces, shells, and returns the
resulting solid fragments.

Relevant files:

- `GEOReverse/Modules/buildSolidCell.py`
- `GEOReverse/Modules/splitFunction.py`

### 6. Classify Each Fragment

After a split, GEOReverse classifies each resulting solid fragment.

For each fragment:

1. `point_inside(...)` finds a representative point inside the solid.
2. `surface_side(point, surf)` evaluates which side of the splitting surface
   the point lies on.
3. The resulting Boolean values are merged with any already-known surface
   classifications.
4. The original CSG expression is evaluated with
   `cellObj.definition.evaluate(pos)`.

The classification result determines what happens next:

- `True`: the fragment is fully inside the target cell and is kept.
- `False`: the fragment is outside and is discarded.
- `None`: the expression cannot yet be fully resolved, so the fragment remains
  a candidate for further splitting by remaining surfaces.

Relevant file:

- `GEOReverse/Modules/splitFunction.py`

### 7. Fuse Kept Fragments

After all required splitting and classification, GEOReverse fuses the accepted
fragments:

```python
fused = parts[0].fuse(parts[1:])
```

It then tries to refine the result with `removeSplitter()` and falls back to a
compound if the fuse does not produce a valid solid.

Relevant file:

- `GEOReverse/Modules/buildSolidCell.py`

### 8. Build Universes and Export CAD

`BuildUniverseCells(...)` recursively builds cells in the requested universe,
handles filled universes, applies transforms, and optionally clips nested cells
to their container.

`CsgToCad.export_cad(...)` creates a FreeCAD document, organizes built solids,
and exports STEP plus a FreeCAD document:

- `.stp` or `.step`
- `.FCStd`

Relevant files:

- `GEOReverse/Modules/buildCAD.py`
- `GEOReverse/core.py`

## Role of Each Component

### GEOReverse

GEOReverse owns the CSG semantics and conversion workflow.

Its responsibilities include:

- parsing MCNP/OpenMC geometry,
- representing cell definitions as Boolean expressions,
- mapping CSG surface ids to internal surface objects,
- computing finite bounding boxes,
- building CAD cutting tools from analytic CSG surfaces,
- choosing the split order,
- classifying split fragments against the original CSG expression,
- handling universes, fills, transforms, and material grouping,
- assembling the final exportable FreeCAD document.

GEOReverse decides what the geometry means.

### FreeCAD

FreeCAD provides the Python CAD API used by GEOReverse.

GEOReverse creates and manipulates FreeCAD `Part` shapes such as:

- boxes,
- faces,
- spheres,
- cylinders,
- cones,
- fused solids,
- document objects.

FreeCAD also provides document management and STEP export through modules such
as `FreeCAD`, `Part`, `BOPTools`, and `Import`.

FreeCAD is the Python-accessible CAD layer.

### OpenCASCADE

OpenCASCADE is the geometric modeling kernel underneath FreeCAD.

It performs the hard BREP operations:

- surface/surface and face/face intersection,
- boolean splitting,
- creation of BREP vertices, edges, faces, shells, and solids,
- validity checks,
- fuse/common operations,
- splitter removal/refinement,
- STEP BREP writing through FreeCAD.

OpenCASCADE computes the actual topology.

## Summary

The CSG-to-CAD engine is best understood as a division of labor:

- GEOReverse parses and interprets CSG.
- FreeCAD exposes CAD construction and export APIs to Python.
- OpenCASCADE computes the BREP topology and boolean results.

The essential algorithm is:

```text
CSG surfaces + cell Boolean expression
        |
        v
bounded stock box
        |
        v
OpenCASCADE split by surface tools
        |
        v
solid fragments
        |
        v
evaluate original CSG expression on each fragment
        |
        v
fuse accepted fragments into CAD solid
```


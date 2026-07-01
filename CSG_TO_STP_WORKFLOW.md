# GEOReverse CSG to STP Workflow

This document describes how the code in `GEOReverse` converts CSG input into STEP/STP output, and how that behavior differs from `GEOReverse_unchanged`.

## Scope

The workflow is implemented around `GEOReverse/core.py`, with format-specific parsing in:

- `GEOReverse/Modules/MCNPinput.py`
- `GEOReverse/Modules/XMLinput.py`
- `GEOReverse/Modules/XMLParser.py`

Solid construction and export are handled primarily by:

- `GEOReverse/Modules/Objects.py`
- `GEOReverse/Modules/buildSolidCell.py`
- `GEOReverse/Modules/buildCAD.py`

The CLI entry point is `GEOReverse/scripts/geouned_csgtocad.py`.

## End-to-End Workflow

### 1. Entry point and configuration

The CLI script reads a JSON config, constructs `geouned.CsgToCad()`, and executes:

1. `read_csg_file(...)`
2. optional `cell_filter(...)`
3. optional `material_filter(...)`
4. `build_universe()`
5. `export_cad(...)`

This path is defined in `GEOReverse/scripts/geouned_csgtocad.py`.

### 2. Input selection and parsing

`CsgToCad.read_csg_file()` in `GEOReverse/core.py` chooses the parser based on `csg_format`:

- `mcnp` -> `McnpInput`
- `openmc_xml` -> `XmlInput`

After instantiating the parser, it performs two required setup passes:

1. `GetSurfaces()`
2. `GetLevelStructure()`

These passes populate:

- primitive CAD-ready surface objects
- universe nesting relationships
- the universe tree used during recursive solid generation

### 3. Surface extraction

Both `McnpInput` and `XmlInput` translate source surface cards into geometry objects such as `Plane`, `Sphere`, `Cylinder`, `Cone`, and `Torus`.

Important details:

- input coordinates are scaled by `10` during import, so geometry is converted into the FreeCAD working units used by the package
- each surface is assigned an internal sequential `id`
- later, cell expressions are rewritten to reference these internal surface ids

For OpenMC XML, this happens in `GEOReverse/Modules/XMLinput.py:GetSurfaces()`.
For MCNP, it happens in `GEOReverse/Modules/MCNPinput.py:GetSurfaces()`.

### 4. Universe graph construction

`GetLevelStructure()` groups cells by universe and determines parent/child universe nesting through `FILL`.

The result is:

- `self.Universes`: `{universe_id: {cell_id: cell_card}}`
- `self.levels`: breadth-by-depth universe structure used to limit recursion

This is the structure consumed later by `build_universe()` and `BuildUniverseCells()`.

### 5. Cell and material filtering

`CsgToCad.cell_filter()` and `CsgToCad.material_filter()` store inclusion or exclusion criteria.

During `GetFilteredCells(...)`, the parser:

1. finds the requested universe and any nested sub-universes up to `depth`
2. filters cells by cell id and material
3. rewrites cell region expressions so referenced surface numbers match the internal surface ids
4. wraps each selected input card in a `CadCell`

`CadCell` is the main working object for downstream geometry construction. It carries:

- the boolean region definition
- assigned surfaces
- fill/universe information
- transforms
- bounding boxes
- the generated FreeCAD shape

### 6. Building the root universe or a container

There are two build modes in `GEOReverse/core.py`:

- `build_universe(...)`: builds a universe in its own coordinate system using a virtual container
- `build_container(cell_label, ...)`: builds the universe contained by a real cell and clips results to that cell

In the normal CLI flow, `build_universe()` is used.

Before recursion starts, the code:

1. determines the starting universe id
2. assigns surfaces to every selected `CadCell` via `AssignSurfaceToCell(...)`
3. computes the deepest allowed nesting level
4. calls `BuildUniverseCells(...)`

### 7. Recursive universe expansion

`GEOReverse/Modules/buildCAD.py:BuildUniverseCells()` is the core recursion step.

For each cell in the current universe, it does one of two things:

#### A. Geometry-bearing cells

If the cell has a region definition:

1. convert the textual boolean expression into `BoolSequence` if needed
2. derive a working bounding box from the current container
3. call `cell.build_BoundBox(...)`
4. call `cell.buildShape(...)`
5. apply the parent transform if needed
6. if building inside a real container, clip the cell solid against the container solid
7. either emit the cell as a leaf solid or recurse into its filled universe

#### B. Fill-only cells

If the cell has no local region definition but does have `FILL`, it acts as a pass-through container:

1. copy the cell
2. compute an inherited bounding box
3. propagate transforms
4. recurse directly into the filled universe

This path is important for lattice-derived and other fill-only structures.

### 8. Bounding box generation

Bounding boxes are used to keep boolean splitting tractable and to define the initial stock solid.

`CadCell.build_BoundBox()` in `GEOReverse/Modules/Objects.py` does the following:

- uses the container box if one already exists
- otherwise falls back to the global universe radius settings
- computes a tighter box from planar constraints when possible
- preserves reversed/forward orientation information

For cells with actual region definitions, that box becomes the seed for solid generation.

### 9. Solid construction from boolean CSG

`CadCell.buildShape()` delegates to `BuildSolid(cell)` in `GEOReverse/Modules/buildSolidCell.py`.

The solid-building flow is:

1. remove undefined surfaces from the cell expression
2. recursively decompose the boolean region through `BuildDepth(...)`
3. create an initial box solid from the current bounding box
4. build shape representations for all referenced surfaces
5. split the current solid by planes first, then by non-planar surfaces
6. keep or discard fragments based on the boolean logic
7. fuse surviving fragments into a final cell solid using `FuseSolid(...)`

The result is a FreeCAD `Shape` stored on the `CadCell`.

### 10. CAD tree assembly

Once recursion finishes, `BuildUniverseCells()` returns a nested structure of:

- universe labels
- leaf `CadCell` solids
- nested child universes

`CsgToCad.export_cad()` then creates a FreeCAD document and assembles exportable objects from `self.buildCAD_list`.

In the current `GEOReverse` version, it uses `makeMaterialTree(...)`, which:

1. walks every leaf cell in the built universe tree
2. groups shapes by material id
3. fuses all solids belonging to the same material
4. creates one exported CAD object per material

### 11. STEP/STP export

`export_cad()` writes:

- a STEP file (`.stp` by default, `.step` also accepted)
- a FreeCAD document (`.FCStd`)

The actual STEP export call is:

- `Import.export(CADdoc.Objects[0:1], output_filename + suffix)`

So the exported STEP file is the top-level `Universes` part containing the generated material-group solids.

## Differences Between `GEOReverse` and `GEOReverse_unchanged`

The source-level differences are concentrated in:

- `core.py`
- `Modules/buildCAD.py`
- `Modules/XMLParser.py`
- `Modules/XMLinput.py`
- `Modules/Objects.py`

`Modules/MCNPinput.py` is unchanged. The meaningful behavior changes are therefore primarily on the OpenMC XML path and in final CAD assembly.

### 1. Export output changed from per-cell grouping to fused per-material solids

In `GEOReverse_unchanged`, `export_cad()` used `makeTree(...)`, which preserved the recursive universe/cell tree and added individual cell solids into material groups.

In `GEOReverse`, `export_cad()` uses `makeMaterialTree(...)` instead.

Effect:

- old behavior: exported structure preserved cell-level objects
- new behavior: all leaf solids are flattened and fused by material
- result: STEP output is materially grouped, with fewer exported solids and less direct traceability back to original cell ids

This is the most visible output change.

### 2. OpenMC XML now supports fill-only cells without a `region`

`GEOReverse_unchanged/Modules/XMLParser.py` assumed every XML cell had a `region`.

`GEOReverse/Modules/XMLParser.py` now allows:

- cells with `fill` and no `region`
- failure only when a cell has neither `region` nor `fill`

Effect:

- old behavior: fill-only XML cells would fail during parsing
- new behavior: such cells are valid and can be used as pure universe containers

This change is reinforced in `CadCell` and `BuildUniverseCells()`, which now handle `definition is None` explicitly.

### 3. OpenMC XML lattice support was added

`GEOReverse/Modules/XMLParser.py` introduces:

- `CellIdAllocator`
- `LatticeCard`
- helper routines for namespace-safe XML tag handling
- translation matrix creation for lattice element placement
- lattice element bounding-box construction

Behavior:

- a `<lattice>` XML node is expanded into synthetic fill-only cells
- each synthetic cell gets:
  - a generated cell id
  - a parent universe
  - a `fill` universe
  - a translation transform
  - a lattice element box used as its local container

Effect:

- `GEOReverse_unchanged` had no lattice expansion path on the XML side
- `GEOReverse` can instantiate repeated universes from OpenMC lattice definitions

### 4. Universe numbering for XML cells changed

In `GEOReverse_unchanged/Modules/XMLParser.py`, a cell with `universe="1"` was remapped to universe `0`.

In `GEOReverse`, the universe id is taken directly:

- `self.U = int(data["universe"])`

Effect:

- old behavior: XML universe `1` was treated specially as the root universe `0`
- new behavior: universe ids are preserved exactly as they appear in the XML

This can change root-universe detection and the resulting universe tree for some XML inputs.

### 5. Recursive build logic now handles pass-through container cells

`GEOReverse/Modules/buildCAD.py` adds explicit logic for cells where:

- `definition is None`
- `FILL` is set

These cells do not generate their own solid. Instead they:

- inherit or derive a local container box
- propagate transforms
- recurse immediately into the filled universe

Effect:

- old behavior: recursion assumed a geometry definition existed before entering most build paths
- new behavior: non-geometric container cells are first-class recursion nodes

This is required for XML fill-only cells and lattices.

### 6. Lattice-aware bounding box handling was added

New helpers in `Modules/buildCAD.py`:

- `get_container_box(...)`
- `get_pass_through_box(...)`
- `get_boundbox_enlarge(...)`

These functions make the recursion aware of:

- local lattice element extents
- inherited parent extents along zero-width lattice axes
- transform reversal when propagating parent boxes through filled universes
- disabling the normal `0.2` bounding-box enlargement for lattice cells

Effect:

- old behavior: only ordinary container-bound boxes were used
- new behavior: lattice child universes can be built within the correct repeated cell envelope

### 7. `CadCell` was hardened for cells without region geometry

`Modules/Objects.py` now supports `CadCell` instances whose `definition` is `None`.

Changes include:

- empty `surfaceList` when no geometry exists
- early returns in `getOuterTerms()`, `cleanUndefined()`, and `__setDefinition__()`
- `build_BoundBox()` accepting `definition is None`
- `copy()` handling absent `solid_plane`, absent surfaces, and absent definition
- a `latticeBox` field for storing local lattice extents

Effect:

- old behavior: several code paths implicitly assumed every cell had a region expression
- new behavior: fill-only and lattice-generated container cells can flow through the same object model safely

### 8. Shape transforms are now applied by copying and mutating the shape

`CadCell.transformSolid()` changed from assigning the result of `transformGeometry(...)` to copying the shape and applying `transformShape(...)`.

Effect:

- this is a lower-level geometric behavior change
- it likely preserves shape handling more robustly for the recursion paths added in `GEOReverse`

### 9. XML surface processing now skips cells with no geometry

`Modules/XMLinput.py:processSurfaces()` now immediately skips cells where `c.geom is None`.

Effect:

- old behavior: fill-only cells would break when the code tried to rewrite surface references
- new behavior: fill-only cells are ignored during surface-number substitution, which is correct because they have no region expression

### 10. XML quadric-to-cylinder/cone recognition is slightly more permissive

In `Modules/XMLinput.py:gq2cyl(...)`, `minRTol` was relaxed from `1.0e-3` to `2.0e-3`.

Effect:

- near-circular quadric inputs are more likely to be recognized as cylinders or cones
- this is a small geometry-tolerance adjustment, not an architectural change

## Practical Summary

Relative to `GEOReverse_unchanged`, the current `GEOReverse` code does three important new things:

1. it supports OpenMC XML fill-only container cells
2. it adds OpenMC lattice expansion and lattice-aware recursive universe building
3. it exports fused solids per material instead of preserving per-cell CAD objects

If the goal is to understand the current CSG-to-STP behavior, the most important files to read in order are:

1. `GEOReverse/core.py`
2. `GEOReverse/Modules/XMLParser.py`
3. `GEOReverse/Modules/XMLinput.py`
4. `GEOReverse/Modules/Objects.py`
5. `GEOReverse/Modules/buildSolidCell.py`
6. `GEOReverse/Modules/buildCAD.py`

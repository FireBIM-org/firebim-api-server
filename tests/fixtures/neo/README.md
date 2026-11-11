# IFC4 semantic fixture

`semantic.ifc` is hand-authored, geometry-free test data, not a converter export.
Its expected facts below are the independent oracle. The deliberate duplicate
property on wall 14 exercises fail-closed handling of conflicting source data.
It is not a claim of complete IFC schema conformance.

| STEP ID | Expected fact |
| --- | --- |
| 10 | Exact `IfcWall`; type supplies `IsExternal=true` and `Reference="from type"`, despite a same-named occurrence Pset containing only `Other`. |
| 11 | Exact `IfcWallStandardCase`; occurrence `IsExternal=false` overrides type `true`. Occurrence length is 1.5 m, overriding type length 1200 mm. |
| 12 | Explicit null `IsExternal` and blank `Reference` mask type values; neither becomes an assertion. |
| 13 | `UnrelatedSet.IsExternal=true` must not satisfy a `Pset_WallCommon` read. |
| 14 | Conflicting `true`/`false` values must not produce a scalar assertion. |
| 15 | Door width 900 mm and explicit `FireExit=true`. |
| 7, 8 | Both spaces belong to the model-scoped compartment `East`; supplied compartment area is 25 m2. Space 7 additionally supplies net area 24 m2; fallback disagreement is diagnostic, not a replacement. |
| 70–72 | Explicit project → building → storey → space aggregation. |
| 73 | Explicit storey containment of walls/door; no inferred room membership. |

Model length units are millimetres; area units are square metres. Root GUIDs end
in the corresponding decimal STEP ID, padded to 22 characters. Converter IRIs,
GUIDs and STEP IDs must remain traceable without relying on `owl:sameAs` reasoning.

`semantic_ifcowl.ttl` is a captured source artifact from the pinned converter,
not the expected destination assertions. Capture configuration and hashes are
recorded in `provenance.json` so the adapter tests do not require a native
executable. See [the implementation notes](../../../../docs/neo-ingestion-implementation.md)
for probe commands, obtained evidence and the production-runtime boundary.
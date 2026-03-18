
# FireBIM Fire Safety Rule System (Conceptual Overview)

## 1. Purpose

This system defines a structured way to represent **fire safety regulations** as machine-readable rules using **SHACL**.  
Rather than using SHACL only for validation, the system uses SHACL as the foundation for a **deterministic rule evaluation engine**.

The goal is to enable automated evaluation of fire safety requirements against a **building graph** derived from BIM data.

The architecture separates three knowledge layers:

1. **Ontology layer (FBO)**  
    Defines concepts, properties, types, and units.
    
2. **Building graph layer**  
    Contains the actual building instance data mapped from **IFC4x3** into the ontology.
    
3. **Rule layer**  
    Contains fire safety regulations expressed as rule elements and rule chains.
    

---

# 2. Data Context

## FireBIM Ontology (FBO)

The **FireBIM Ontology (FBO)** provides the semantic definitions used by rules.

Examples include:

- `fbo:buildingHeight`
    
- `fbo:evacuationDistance`
    
- `fbo:fireClass`
    

These definitions include:

- expected datatype
    
- units
    
- semantic meaning
    

Example:

```
fbo:buildingHeight
    rdf:type owl:DatatypeProperty ;
    rdfs:range unit:Meter .
```

The ontology therefore defines **what properties mean** and **how they should be interpreted**.

---

## Building Graph

The **building graph** represents a specific building instance.

It is assumed that:

- building data originates from **IFC4x3**
    
- the IFC model is mapped into the **FBO ontology**
    
- required values are present in the graph
    

Example:

```
ex:Building_01
    fbo:buildingHeight "28"^^unit:Meter .
```

The rule system evaluates this graph.

---

# 3. Rule Architecture

The fire safety rule system consists of two components:

|Component|Purpose|
|---|---|
|**fbRuleElement**|Atomic decision rule|
|**fbRule**|Composed rule chain|

---

# 4. fbRuleElement (Atomic Rule)

An **fbRuleElement** represents the smallest decision unit.

It is implemented as a SHACL shape with a controlled structure.

Each rule element:

- evaluates a condition
    
- produces a **three-state result**
    

```
True
False
Unknown
```

The result determines the **next rule element to evaluate**.

Conceptually the rule behaves as a **decision node**.

```
Input graph
      │
      ▼
   SHACL Shape
      │
 ┌────┼─────┐
 │    │     │
True False Unknown
 │    │     │
 ▼    ▼     ▼
Next Next  Next
```

### Interpretation of results

|Result|Meaning|
|---|---|
|**True**|Rule condition satisfied|
|**False**|Rule condition violated|
|**Unknown**|Required information missing or not evaluable|

The **Unknown** state is essential because regulatory checks often depend on data that may not yet be provided.

---

# 5. fbRule (Composite Rule)

An **fbRule** represents a full fire safety rule composed of multiple rule elements.

It defines:

- an **entry rule element**
    
- the network of connected rule elements
    

The evaluation proceeds step-by-step through the decision graph.

Example conceptual structure:

```
fbRule:BuildingHeightClassification

Start
  │
  ▼
CheckHeight
  │
 ├─ True   → CheckEvacuationDistance
 ├─ False  → HighRiskClassification
 └─ Unknown → RequestMissingData
```

This structure is essentially a **rule graph**.

---

# 6. Conceptual Model

```
fbRule
   │
   ▼
fbRuleElement
   │
   ├─ condition
   │
   ├─ truePath
   ├─ falsePath
   └─ unknownPath
```

Each path references another **fbRuleElement** or a final rule outcome.

The result is a **directed decision graph**.

---

# 7. Relation to Decision Systems

Although implemented in SHACL, the system behaves conceptually like:

- a **decision tree**
    
- a **rule engine**
    
- an **inference graph**
    

However, SHACL provides important advantages:

- deterministic evaluation
    
- explicit rule structure
    
- transparent reasoning
    
- compatibility with RDF graphs
    
- explainable results
    

This makes SHACL suitable for modelling **regulatory logic**.

---

# 8. Shape Storage and Reuse

Rule elements and rules are stored on a **graph server**.

Two types of resources are stored:

### Atomic rule elements

```
/rules/elements/{id}
```

Example:

```
fbRuleElement_height_limit
```

Each stored as an independent TTL resource.

---

### Composite rules

```
/rules/{id}
```

Example:

```
fbRule_building_height_classification
```

These reference multiple rule elements.

---

# 9. Evaluation Workflow

The full rule evaluation process is:

```
IFC Model
    │
    ▼
IFC → FBO Mapping
    │
    ▼
Building Graph
    │
    ▼
fbRule Evaluation
    │
    ▼
Decision Path
    │
    ▼
Final Classification / Explanation
```

---

# 10. Key Design Principles

The system follows several core principles.

### Atomic rule elements

Each rule element performs **one clearly defined check**.

---

### Explicit branching

Every rule element defines all three paths:

- True
    
- False
    
- Unknown
    

This prevents ambiguous evaluation states.

---

### Deterministic evaluation

Given the same building graph, the rule system always produces the same result.

---

### Explainability

The evaluation path forms a traceable explanation of the rule result.

---

# 11. Summary

The FireBIM rule system is a **semantic rule engine built on SHACL**.

Key concepts:

- **fbRuleElement**  
    Atomic rule node implemented as a SHACL shape.
    
- **fbRule**  
    A network of rule elements forming a regulatory decision graph.
    

The system evaluates **FBO-aligned building graphs** derived from IFC models and produces deterministic, explainable rule outcomes.

This approach enables structured, machine-readable modelling of fire safety regulations.
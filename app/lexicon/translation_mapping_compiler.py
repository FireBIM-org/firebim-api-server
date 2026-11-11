from __future__ import annotations

import re

from rdflib import Literal, URIRef
from rdflib.namespace import RDF, RDFS

from app.lexicon.ifc4_schema import attribute_datatype
from app.lexicon.ifc_mapping_values import NUMERIC_DATATYPES, XSD
from app.lexicon.translation_mapping_policy import EXCLUDED_DECLARATIONS, approved_rule, final_targets
from app.schemas.translation import TranslationValidationDiagnostic

IFC_IRI = "https://firebim.org/ontologies/fbo/ifc#"
# Reviewed explanatory annotations, not a blanket exemption for interpretation text.
EXPLANATORY_ANNOTATIONS = {
    (
        IFC_IRI + "Building",
        "hasInterpretationResult",
    ): "One or more buildings can exist in each IFC file (although typically not supported by modelling software)",
    (
        IFC_IRI + "FireCompartment1",
        "hasArgumentation",
    ): "This seems (in general) the preferred entity in IFC to capture a compartment of any kind.",
    (IFC_IRI + "FireCompartment1", "hasInterpretationResult"): "Modelled compartment",
    (IFC_IRI + "FireCompartment2a", "hasInterpretationResult"): "Modelled compartment",
    (
        IFC_IRI + "AreaFireCompartment2a",
        "hasInterpretationResult",
    ): "If not given by a quantity, it could be extracted from geometry (FootPrint or Body)",
    (
        IFC_IRI + "HeightBuilding",
        "hasArgumentation",
    ): "Cannot use IfcBuilding.Qto_BuildingBaseQuantities.Height (different measurement method)",
    (
        IFC_IRI + "HeightBuilding",
        "hasInterpretationResult",
    ): "Reference height, as derived from the measurement method of the national regulation",
    (
        IFC_IRI + "EscapeRoute",
        "hasArgumentation",
    ): "We have seen examples where they are modelled with other IFC entities (proxy, slab, …)",
    (IFC_IRI + "BuildingLayersBuilding", "hasArgumentation"): "Different from number of layers in a compartment",
    (
        IFC_IRI + "BuildingLayersBuilding",
        "hasInterpretationResult",
    ): "Number of building layers or storeys in the building",
}


def _seed_value(value, datatype):
    if (
        not isinstance(value, Literal)
        or value.language
        or value.datatype
        not in (
            None,
            URIRef(XSD + "string"),
            URIRef(datatype),
        )
    ):
        raise ValueError("Constraint literals must have the declared source datatype and no language tag.")
    text = str(value)
    if "<" in text or ">" in text:
        raise ValueError("Placeholder values need interpretation, not executable literal constraints.")
    if datatype == XSD + "boolean":
        if text not in {"True", "False", "true", "false", "1", "0"}:
            raise ValueError("Boolean constraints must explicitly be true/false or 1/0.")
        return text in {"True", "true", "1"}
    if datatype == XSD + "integer":
        if not re.fullmatch(r"[+-]?[0-9]+", text):
            raise ValueError("Integer constraints require an exact integer, never a parsed measurement.")
        return int(text)
    return text


def compile_module_suggestion(mapping, modules, path):
    """Adapt only unchanged declarations from verified selected releases, never guessed seed categories."""
    from app.lexicon.translation_mapping_extraction import FBO, IFC, snapshot_module_suggestion

    diagnostics = []

    def error(code, message):
        diagnostics.append(
            TranslationValidationDiagnostic(
                code=code,
                severity="error",
                mapping_id=mapping.id,
                field_path=path,
                message=message,
            )
        )
        return None, diagnostics

    def decision(code, message):
        return error(code, f"Decision needed for {declaration_iri} → {target_iri}: {message}")

    provenance = mapping.seed_provenance or {}
    origin = provenance.get("module")
    if provenance.get("kind") != "ontology_module" or not isinstance(origin, dict):
        return error(
            "mapping_provenance_required",
            "Module suggestions need their original verified module provenance; "
            "use an explicit executable definition for manual authoring.",
        )
    candidates = [
        module
        for module in modules
        if all(
            origin.get(field) == getattr(module.pin, field)
            for field in ("family", "country", "version", "ontology_iri", "version_iri", "sha256")
        )
    ]
    declaration_iri, target_iri = provenance.get("declaration_iri"), provenance.get("target_iri")
    if len(candidates) != 1 or not isinstance(declaration_iri, str) or not isinstance(target_iri, str):
        return error(
            "mapping_seed_changed",
            "Select the original exact module release and digest. "
            "Changed or missing provenance cannot be silently recompiled.",
        )
    module = candidates[0]
    declaration, target = URIRef(declaration_iri), URIRef(target_iri)
    owners = sorted(
        (owner for owner in modules if (target, None, None) in owner.graph),
        key=lambda owner: (owner.pin.family, owner.pin.country, owner.pin.version),
    )
    if not owners or not (
        (declaration, IFC.mapsToTerm, target) in module.graph
        or any((target, FBO.hasIFCMapping, declaration) in owner.graph for owner in owners)
    ):
        return error("mapping_seed_changed", "The declaration/target link is absent from the selected releases.")
    # Rebuild from the verified bytes so edited suggestion JSON cannot bypass source evidence or review issues.
    original = snapshot_module_suggestion("IFC4", module, declaration, target, owners)
    if mapping.definition != original.definition or provenance != original.seed_provenance:
        return error(
            "mapping_seed_modified",
            "The imported snapshot differs from the selected releases. Preserve it for review or author "
            "an explicit ifc4-mapping/1 definition; validation never overwrites it.",
        )
    definition = original.definition
    if declaration_iri in EXCLUDED_DECLARATIONS:
        diagnostics.append(
            TranslationValidationDiagnostic(
                code="mapping_excluded_by_agreement",
                severity="info",
                mapping_id=mapping.id,
                field_path=path,
                message=f"Excluded, not compiled: {declaration_iri} → {target_iri}. "
                f"{EXCLUDED_DECLARATIONS[declaration_iri]} "
                "The saved enabled/review state and original snapshot are unchanged.",
            )
        )
        return None, diagnostics
    source = definition["source"]
    rule = approved_rule(module, declaration_iri, target_iri, source)
    # Recheck old review flags against the verified RDF without changing saved snapshots.
    issues = set(definition["review_issues"]) - {
        "mapping_semantics_unavailable",
        "source_schema_compatibility_unverified",
        "mapping_selector_unsupported",
        "mapping_interpretation_required",
        "mapping_target_range_unresolved",
    }
    if issues:
        return error(
            "mapping_module_declaration_unsupported",
            "This declaration still requires interpretation: "
            + ", ".join(sorted(issues))
            + ". Keep it disabled and unresolved.",
        )
    allowed = {
        RDF.type,
        RDFS.label,
        RDFS.comment,
        IFC.hasClassPredefinedType,
        IFC.hasPsetProperty,
        IFC.hasDataType,
        IFC.hasValue,
        IFC.hasKind,
        IFC.mapsToTerm,
        IFC.isBuildingSMART,
        IFC.hasArgumentation,
        IFC.hasInterpretationResult,
    }
    if any(predicate not in allowed for predicate, _ in module.graph.predicate_objects(declaration)):
        return error(
            "mapping_module_declaration_unsupported",
            "The declaration contains unsupported predicates; their semantics must not be dropped.",
        )
    if declaration in {IFC.ActiveFireProtection, IFC.AutomaticDetection} or (
        declaration == IFC.AutomaticFireExtinguishSystem and definition["target"]["kind"] == "class"
    ):
        return decision(
            "mapping_system_definition_required",
            "Preserve object/system modeling: FBO must specify which source establishes which system, "
            "its entity identity and connecting object property. Do not combine protection flags, "
            "equate alarm/warning systems, "
            "or infer sprinkler-specific evidence from a generic extinguishing flag.",
        )
    if declaration == IFC.BuildingLayersBuilding and definition["target"]["kind"] == "class":
        return decision(
            "mapping_ontology_correction_required",
            "The approved result is a numeric storey count, but BuildingLayers is declared as a class. "
            "Correct the ontology or approve an explicit executable override; "
            "no pinned class is silently rewritten as a datatype property.",
        )
    approved_annotations = {
        ("FireCompartment2b", "hasInterpretationResult"): "Aggregate by this value",
        (
            "AutomaticFireExtinguishSystem",
            "hasInterpretationResult",
        ): "Is this targeting sprinklers or is this more generic and would others also apply?",
        ("WidthFreeDoors", "hasArgumentation"): "To be combined with the identification of escape door or fire exits",
    }
    for predicate in (IFC.hasArgumentation, IFC.hasInterpretationResult):
        name = str(predicate)[len(IFC_IRI) :]
        for value in sorted(module.graph.objects(declaration, predicate), key=lambda item: item.n3()):
            if not (
                isinstance(value, Literal)
                and not value.language
                and value.datatype in (None, URIRef(XSD + "string"))
                and (
                    EXPLANATORY_ANNOTATIONS.get((declaration_iri, name)) == str(value)
                    or rule is not None
                    and approved_annotations.get((rule, name)) == str(value)
                )
            ):
                return decision(
                    "mapping_interpretation_required",
                    f"Resolve the module's {name} annotation {value.n3()} "
                    "before treating this declaration as executable. "
                    "Explanatory notes are accepted only after explicit review; no prose is executed.",
                )
            diagnostics.append(
                TranslationValidationDiagnostic(
                    code="mapping_annotation_preserved",
                    severity="info",
                    mapping_id=mapping.id,
                    field_path=path,
                    message=f"{declaration_iri}: preserved explanatory note: {value}. "
                    "Only explicit selectors and documented approved pilot decisions are compiled; "
                    "no prose or geometry fallback is executed.",
                )
            )
    destination = definition["target"]
    effective_target_iri = target_iri
    finals = final_targets(rule, target_iri, modules)
    if len(finals) > 1:
        return decision(
            "mapping_final_target_ambiguous",
            f"Multiple final-module targets are selected: {finals}. "
            "Select one unambiguous final target; list order is not precedence.",
        )
    if finals:
        effective_target_iri = finals[0]
        final_owners = sorted(
            (owner for owner in modules if (URIRef(effective_target_iri), None, None) in owner.graph),
            key=lambda owner: (owner.pin.family, owner.pin.country, owner.pin.version),
        )
        destination = snapshot_module_suggestion(
            "IFC4", module, declaration, URIRef(effective_target_iri), final_owners
        ).definition["target"]
        diagnostics.append(
            TranslationValidationDiagnostic(
                code="mapping_final_target_resolved",
                severity="info",
                mapping_id=mapping.id,
                field_path=path,
                message=f"Final-module policy: {target_iri} → {effective_target_iri}; "
                "use its actual declaration and datatype. The original snapshot is retained.",
            )
        )
    if destination["kind"] == "datatype_property" and (
        len(destination["ranges"]) != 1 or not destination["ranges"][0].startswith(XSD)
    ):
        return decision(
            "mapping_target_range_unresolved",
            "Declare one exact destination datatype range in the ontology. "
            "For a measurement, also choose its unit and measurement meaning.",
        )
    for predicate in (IFC.hasClassPredefinedType, IFC.hasPsetProperty, IFC.hasDataType):
        if any(
            not isinstance(value, Literal) or value.language or value.datatype not in (None, URIRef(XSD + "string"))
            for value in module.graph.objects(declaration, predicate)
        ):
            return error(
                "mapping_selector_unsupported",
                "Source declarations must be plain string literals; "
                "RDF identities and language tags cannot be discarded.",
            )
    if rule in {"TeachingZone", "FireCompartment2b"} and any(
        not isinstance(value, Literal) or value.language or value.datatype not in (None, URIRef(XSD + "string"))
        for value in module.graph.objects(declaration, IFC.hasValue)
    ):
        return error(
            "mapping_literal_invalid",
            "Approved teaching-zone/name rules require the original plain string values; "
            "literal types and language tags cannot be discarded.",
        )
    classes = sorted({item.strip() for value in source["class_predefined_types"] for item in value.split(",")})
    if not 1 <= len(classes) <= 32 or any(
        not re.fullmatch(r"Ifc[A-Za-z0-9_]+(?:\.[A-Z0-9_]+)?", value) for value in classes
    ):
        return error(
            "mapping_selector_unsupported",
            "Use at most 32 explicit IFC4 classes with optional predefined types, "
            "not wildcards, placeholders or arbitrary paths.",
        )
    selectors = []
    for value in classes:
        class_name, _, predefined = value.partition(".")
        selectors.append(
            {
                "schema": "IFC4",
                "ifc_class": class_name,
                "match": "exact",
                **({"predefined_type": predefined} if predefined else {}),
            }
        )
    executable = {
        "language": "ifc4-mapping/1",
        "source": selectors[0] if len(selectors) == 1 else {"schema": "IFC4", "any_of": selectors},
        "subject": "entity",
        "policies": {
            "identity": "model_scoped",
            "property_precedence": "occurrence_over_type",
            "missing": "skip",
            "invalid": "error",
            "conflicts": "error",
        },
    }
    selector = None
    fallback = []
    measurement = rule in {"AreaFireCompartment1", "AreaFireCompartment2a", "HeightBuilding", "WidthFreeDoors"}
    if source["pset_properties"]:
        if rule == "AreaFireCompartment1":
            property_name = "CPset_FireBIM.CompartmentArea"
        elif len(source["pset_properties"]) != 1 or "," in source["pset_properties"][0]:
            return decision(
                "mapping_selector_unsupported",
                "Choose the combination rule for source fields "
                f"{source['pset_properties']}: fallback with a declared priority, agreement, or Boolean AND/OR. "
                "No first-value-wins rule is inferred.",
            )
        else:
            property_name = source["pset_properties"][0]
        if not measurement and any(value in {"Area", "Length", "Volume", "Mass"} for value in source["data_types"]):
            return decision(
                "mapping_units_required",
                f"Choose the source/output unit and measurement meaning for {property_name} "
                f"({', '.join(source['data_types'])}). Confirm whether the supplied value is authoritative; "
                "no geometry calculation or substitute measurement is inferred.",
            )
        if re.fullmatch(r"(?:C?Pset_)[A-Za-z0-9_]+\.[A-Za-z_][A-Za-z0-9_]*", property_name):
            pset, name = property_name.split(".")
            selector = {"kind": "property", "pset": pset, "name": name}
        elif measurement and re.fullmatch(r"Qto_[A-Za-z0-9_]+\.[A-Za-z_][A-Za-z0-9_]*", property_name):
            pset, name = property_name.split(".")
            selector = {"kind": "quantity", "pset": pset, "name": name}
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", property_name):
            selector = {"kind": "attribute", "name": property_name}
            if any(attribute_datatype(member["ifc_class"], property_name) is None for member in selectors):
                return decision(
                    "mapping_attribute_unsupported",
                    f"{property_name} is not a supported scalar attribute on every selected IFC4 class. "
                    "Specify the intended IFC4 attribute or exact property-set/property; "
                    "names are not silently corrected.",
                )
        else:
            return decision(
                "mapping_selector_unsupported",
                f"Replace {property_name!r} with an exact IFC4 attribute, property/quantity selector "
                "or explicit classification-reference binding. "
                "A classification system and code cannot be inferred from prose.",
            )
        source_types = {"Boolean": XSD + "boolean", "String": XSD + "string", "Integer": XSD + "integer"}
        if measurement:
            if destination["kind"] != "datatype_property" or destination["ranges"][0] not in NUMERIC_DATATYPES:
                return error(
                    "mapping_target_datatype_mismatch",
                    "Approved measurements require an actual numeric destination datatype property.",
                )
            selector["datatype"] = destination["ranges"][0]
            selector["unit"] = "ifc_area" if source["data_types"] == ["Area"] else "ifc_length"
            if rule == "AreaFireCompartment1":
                fallback = [
                    {
                        **selector,
                        "kind": "quantity",
                        "pset": "Qto_SpatialZoneOtherBaseQuantities",
                        "name": "NetFloorArea",
                    }
                ]
        elif len(source["data_types"]) != 1 or source["data_types"][0] not in source_types:
            return error(
                "mapping_source_datatype_mismatch",
                "Declare Boolean, String or a unitless Integer count. "
                "Measures and unit conversions require explicit approved definitions.",
            )
        else:
            selector["datatype"] = source_types[source["data_types"][0]]
        if selector["datatype"] == XSD + "integer" and not measurement:
            selector["unit"] = "unitless"
        if rule == "TeachingZone":
            executable["condition"] = {"kind": "in", "selector": selector, "values": ["EDUCATION", "TEACHING"]}
        elif source["values"] and rule != "FireCompartment2b":
            try:
                values = [
                    _seed_value(value, selector["datatype"])
                    for value in sorted(module.graph.objects(declaration, IFC.hasValue), key=str)
                ]
            except ValueError as exc:
                code = (
                    "mapping_interpretation_required"
                    if any("<" in v or ">" in v for v in source["values"])
                    else "mapping_literal_invalid"
                )
                if code == "mapping_interpretation_required":
                    return decision(
                        code,
                        f"{exc} Specify the actual classification values or a nonempty-name/presence rule; "
                        "a placeholder is not a value.",
                    )
                return error(code, str(exc))
            executable["condition"] = {"kind": "in", "selector": selector, "values": values}
    elif source["data_types"] or source["values"]:
        return error(
            "mapping_selector_unsupported",
            "Source datatype/value constraints need an explicit value selector; they cannot be discarded.",
        )
    if destination["kind"] == "class":
        if rule == "FireCompartment2b":
            executable["target"] = {"kind": "class", "iri": effective_target_iri, "grouping": selector}
        elif selector is not None and "condition" not in executable:
            return decision(
                "mapping_condition_required",
                "Specify which typed source values qualify the selected entity for the destination class. "
                "If the source is a count or system flag, confirm whether the target should instead "
                "be a datatype property "
                "or a separate linked entity. Presence, Boolean truth and new system entities are not inferred.",
            )
        else:
            executable["target"] = {"kind": "class", "iri": effective_target_iri}
    elif destination["kind"] == "datatype_property":
        if selector is None or len(destination["ranges"]) != 1:
            return decision(
                "mapping_selector_unsupported",
                "Specify the source field and values that qualify an entity for this output. "
                "A Boolean destination does not mean every selected space is true; "
                "a value mapping needs an exact scalar selector.",
            )
        datatype = destination["ranges"][0]
        transform = "identity"
        if rule == "TeachingZone":
            executable["target"] = {
                "kind": "datatype_property",
                "iri": effective_target_iri,
                "datatype": datatype,
                "cardinality": "single",
                "value": {"kind": "constant", "value": True},
            }
        elif selector["datatype"] != datatype:
            if selector["datatype"] == XSD + "integer" and datatype in NUMERIC_DATATYPES:
                transform = "numeric_cast"
            else:
                return decision(
                    "mapping_source_datatype_mismatch",
                    "Specify an explicit source-to-destination value conversion. "
                    "For String-to-Boolean classification, approve the exact matching values, "
                    "case policy and nonmatching behavior. "
                    "Comma-separated value literals are not split implicitly; "
                    "Boolean/string/measure conversions are not inferred.",
                )
        if rule != "TeachingZone":
            executable["target"] = {
                "kind": "datatype_property",
                "iri": effective_target_iri,
                "datatype": datatype,
                "cardinality": "single",
                "value": {
                    "kind": "read",
                    "selector": selector,
                    "transform": transform,
                    **({"fallback": fallback} if fallback else {}),
                },
            }
        if rule == "WidthFreeDoors":
            executable["condition"] = {
                "kind": "equals",
                "selector": {
                    "kind": "property",
                    "pset": "Pset_DoorCommon",
                    "name": "FireExit",
                    "datatype": XSD + "boolean",
                },
                "values": [True],
            }
    else:
        return decision(
            "mapping_relationship_unsupported",
            "The destination is an object property, but this declaration supplies a scalar/entity selector. "
            "Should the ontology use a datatype property for this value, "
            "or which explicit relation and endpoint entity should it link? "
            "Object-property ranges cannot be literal datatypes. No endpoint is invented.",
        )
    if rule:
        diagnostics.append(
            TranslationValidationDiagnostic(
                code="mapping_decision_applied",
                severity="info",
                mapping_id=mapping.id,
                field_path=path,
                message=f"Applied approved IFC4 pilot rule for {declaration_iri}. "
                "Supplied measurements retain IFC units (SI only when absent); "
                "no geometry fallback or unapproved source combination is performed. "
                "Teaching-zone matches and compartment grouping are exact and model-scoped.",
            )
        )
    diagnostics.append(
        TranslationValidationDiagnostic(
            code="module_mapping_compiled",
            severity="info",
            mapping_id=mapping.id,
            field_path=path,
            message="The unchanged module declaration was compiled for IFC4 selector checks using exact-type matching. "
            "This validates only this declaration, not blanket IFC4 compatibility of the mapping module.",
        )
    )
    return executable, diagnostics

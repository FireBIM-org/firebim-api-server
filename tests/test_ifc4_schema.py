import unittest

from app.lexicon import ifc4_schema


class Ifc4SchemaTests(unittest.TestCase):
    def test_entity_identities_and_inheritance_are_exact_ifc4(self):
        for name in (
            "IfcWall",
            "IfcWallStandardCase",
            "IfcBuildingStorey",
            "IfcRelAggregates",
            "IfcRelContainedInSpatialStructure",
            "IfcCartesianTransformationOperator3DnonUniform",
        ):
            with self.subTest(name=name):
                self.assertTrue(ifc4_schema.is_entity(name))
                self.assertTrue(ifc4_schema.is_subtype(name, name))

        self.assertTrue(ifc4_schema.is_subtype("IfcWallStandardCase", "IfcWall"))
        self.assertTrue(ifc4_schema.is_subtype("IfcWallStandardCase", "IfcRoot"))
        self.assertTrue(ifc4_schema.is_subtype("IfcBuildingStorey", "IfcSpatialStructureElement"))
        self.assertTrue(ifc4_schema.is_subtype("IfcBuildingStorey", "IfcSpatialElement"))
        self.assertTrue(ifc4_schema.is_subtype("IfcRelAggregates", "IfcRelDecomposes"))
        self.assertTrue(ifc4_schema.is_subtype("IfcRelContainedInSpatialStructure", "IfcRelConnects"))
        self.assertFalse(ifc4_schema.is_subtype("IfcWall", "IfcWallStandardCase"))
        self.assertFalse(ifc4_schema.is_subtype("IfcWall", "IfcSpatialElement"))

        for name in (
            "IfcFacility",
            "IfcBuiltElement",
            "IfcBridge",
            "IfcAnything",
            "IfcLabel",
            "IfcWallTypeEnum",
            "ifcwall",
            "IFCWALL",
            "IfcWall ",
            "",
            "IfcUnknown",
            "IfcCartesianTransformationOperator3DNonUniform",
        ):
            with self.subTest(name=name):
                self.assertFalse(ifc4_schema.is_entity(name))
                self.assertFalse(ifc4_schema.is_subtype(name, name))
                self.assertFalse(ifc4_schema.is_subtype(name, "IfcRoot"))
                self.assertFalse(ifc4_schema.is_subtype("IfcWall", name))

    def test_scalar_attributes_are_inherited_case_sensitive_and_unit_safe(self):
        xsd = "http://www.w3.org/2001/XMLSchema#"
        supported = (
            ("IfcWall", "Name", "string"),
            ("IfcWall", "GlobalId", "string"),
            ("IfcWallStandardCase", "Name", "string"),
            ("IfcWallStandardCase", "GlobalId", "string"),
            ("IfcBuildingStorey", "LongName", "string"),
            ("IfcRelAggregates", "Description", "string"),
            ("IfcSurfaceTexture", "RepeatS", "boolean"),
            ("IfcFaceBound", "Orientation", "boolean"),
            ("IfcStairFlight", "NumberOfRisers", "integer"),
            ("IfcGeometricRepresentationContext", "CoordinateSpaceDimension", "integer"),
            ("IfcCartesianTransformationOperator3D", "Scale", "double"),
            ("IfcColourRgb", "Red", "double"),
        )
        for entity, attribute, datatype in supported:
            with self.subTest(entity=entity, attribute=attribute):
                self.assertEqual(ifc4_schema.attribute_datatype(entity, attribute), xsd + datatype)

        unsupported = (
            ("IfcWall", "UnknownAttribute"),
            ("IfcWall", "name"),
            ("IfcWall", "Name "),
            ("ifcwall", "Name"),
            ("IfcFacility", "Name"),
            ("IfcWall", "PredefinedType"),
            ("IfcWall", "OwnerHistory"),
            ("IfcWall", "IsDefinedBy"),
            ("IfcRelAggregates", "RelatedObjects"),
            ("IfcRelContainedInSpatialStructure", "RelatingStructure"),
            ("IfcPropertySingleValue", "NominalValue"),
            ("IfcCartesianPoint", "Coordinates"),
            ("IfcBSplineCurve", "ClosedCurve"),
            ("IfcMaterialLayer", "IsVentilated"),
            ("IfcDoor", "OverallHeight"),
            ("IfcMaterialLayer", "LayerThickness"),
            ("IfcBuildingStorey", "Elevation"),
            ("IfcQuantityArea", "AreaValue"),
            ("IfcQuantityTime", "TimeValue"),
            ("IfcQuantityWeight", "WeightValue"),
            ("IfcGeometricRepresentationContext", "Precision"),
            ("IfcGeometricRepresentationSubContext", "CoordinateSpaceDimension"),
            ("IfcConversionBasedUnitWithOffset", "ConversionOffset"),
            ("IfcLightSourcePositional", "DistanceAttenuation"),
            ("IfcLightSourcePositional", "QuadricAttenuation"),
            ("IfcCurrencyRelationship", "ExchangeRate"),
        )
        for entity, attribute in unsupported:
            with self.subTest(entity=entity, attribute=attribute):
                self.assertIsNone(ifc4_schema.attribute_datatype(entity, attribute))

    def test_predefined_types_are_the_declared_enum_and_inherited(self):
        wall_types = frozenset(
            {
                "MOVABLE",
                "PARAPET",
                "PARTITIONING",
                "PLUMBINGWALL",
                "SHEAR",
                "SOLIDWALL",
                "STANDARD",
                "POLYGONAL",
                "ELEMENTEDWALL",
                "USERDEFINED",
                "NOTDEFINED",
            }
        )
        for entity in ("IfcWall", "IfcWallType", "IfcWallStandardCase", "IfcWallElementedCase"):
            with self.subTest(entity=entity):
                self.assertIsInstance(ifc4_schema.predefined_types(entity), frozenset)
                self.assertEqual(ifc4_schema.predefined_types(entity), wall_types)
                self.assertNotIn("NOT_AN_ENUM", ifc4_schema.predefined_types(entity))
                self.assertNotIn("solidwall", ifc4_schema.predefined_types(entity))
                self.assertNotIn("RETAININGWALL", ifc4_schema.predefined_types(entity))

        self.assertIn("INTERNAL", ifc4_schema.predefined_types("IfcSpace"))
        self.assertIn("DOOR", ifc4_schema.predefined_types("IfcDoorStandardCase"))
        self.assertIn("USERDEFINED", ifc4_schema.predefined_types("IfcDistributionCircuit"))
        for entity in (
            "IfcRoot",
            "IfcBuildingStorey",
            "IfcRelAggregates",
            "IfcActorRole",
            "IfcFacility",
            "IfcAnything",
            "ifcwall",
            "IfcWall ",
            "",
        ):
            with self.subTest(entity=entity):
                self.assertEqual(ifc4_schema.predefined_types(entity), frozenset())

    def test_provenance_identifies_the_exact_source_edition(self):
        provenance = ifc4_schema.SCHEMA_PROVENANCE
        self.assertEqual(provenance["schema"], "IFC4")
        self.assertEqual(provenance["edition"], "IFC4 ADD2 TC1")
        self.assertEqual(provenance["issue_date"], "2017-10-29")
        self.assertEqual(provenance["publisher"], "buildingSMART International Limited")
        self.assertEqual(
            provenance["sha256"],
            "a2704ba20a1b3d0b7d9b61d6fd37d0baa3b4996ba3e90d968a1d2ca2819d1046",
        )
        self.assertIn("/IFC4/ADD2_TC1/EXPRESS/IFC4_ADD2_TC1.exp", provenance["source"])
        self.assertIn("/119bf71c8049cd0683df0109844605e975025db2/", provenance["mirror"])

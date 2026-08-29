import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fbx_ascii

# Every shape the parser has to get right, in the layout the FBX SDK actually writes.
# The Vertices array holds whole numbers written without a decimal point, which is the
# case that makes inferring an array's type from its values wrong. The DocumentUrl holds
# a semicolon, which is the comment character everywhere outside a string. The Model's id
# is too large for int32 and the Geometry's is not, which is why both must still read as an
# id slot rather than by magnitude. The Model's Shading is the FBX SDK's bare-letter char
# property, seen outside Properties70 in every real ASCII file Synty shipped.
SAMPLE = """; FBX 7.4.0 project file
; ----------------------------------------------------

FBXHeaderExtension:  {
\tFBXVersion: 7400
\tSceneInfo: "SceneInfo::GlobalInfo", "UserData" {
\t\tType: "UserData"
\t}
}
Objects:  {
\tGeometry: 140718, "Geometry::", "Mesh" {
\t\tVertices: *6 {
\t\t\ta: 0,0,0,1,
\t\t\t2,3
\t\t}
\t\tPolygonVertexIndex: *3 {
\t\t\ta: 0,1,-3
\t\t}
\t\tEdges: *0 {
\t\t}
\t\tLayerElementUV: 0 {
\t\t\tName: "map1"
\t\t}
\t}
\tModel: 1852219572480, "Model::Light_Shaft_Base", "LimbNode" {
\t\tShading: T
\t\tProperties70:  {
\t\t\tP: "UnitScaleFactor", "double", "Number", "",1
\t\t\tP: "filmboxTypeID", "Short", "", "A+UH",5,5,5
\t\t\tP: "Original", "Compound", "", ""
\t\t\tP: "DocumentUrl", "KString", "Url", "", "U:/Dropbox/a;b.fbx"
\t\t\tP: "Freeze", "Whatsit", "", "",2.5
\t\t}
\t}
\tTexture: 1168994800, "Texture::file1178", "" {
\t\tMedia: "Video::file1178"
\t\tTextureName: "Texture::file1178"
\t}
}
Connections:  {
\tC: "OO",1852219572480,0
}
"""


def find(elements, name):
    """The first element with this id, at any depth."""
    for element in elements:
        if element.id == name:
            return element
        found = find(element.elems, name)
        if found is not None:
            return found
    return None


class TestSerialization(unittest.TestCase):
    def test_binary_magic_is_recognized(self):
        self.assertTrue(fbx_ascii.is_binary(b"Kaydara FBX Binary  \x00\x1a\x00"))

    def test_ascii_header_is_not_binary(self):
        self.assertFalse(fbx_ascii.is_binary(b"; FBX 7.4.0 project file\n"))

    def test_short_file_is_not_binary(self):
        self.assertFalse(fbx_ascii.is_binary(b"Kaydara"))


class TestStructure(unittest.TestCase):
    def setUp(self):
        self.elements, self.version = fbx_ascii.parse(SAMPLE)

    def test_version_comes_from_the_header(self):
        self.assertEqual(self.version, 7400)

    def test_top_level_nodes_are_in_source_order(self):
        self.assertEqual([element.id for element in self.elements],
                         ["FBXHeaderExtension", "Objects", "Connections"])

    def test_nesting_is_preserved(self):
        objects = find(self.elements, "Objects")
        self.assertEqual([element.id for element in objects.elems],
                         ["Geometry", "Model", "Texture"])

    def test_comments_do_not_become_nodes(self):
        self.assertIsNone(find(self.elements, "FBX"))

    def test_a_semicolon_inside_a_string_is_not_a_comment(self):
        row = find(self.elements, "Properties70").elems[3]
        self.assertEqual(row.props[4], "U:/Dropbox/a;b.fbx")


class TestScalarTyping(unittest.TestCase):
    def setUp(self):
        self.elements, _ = fbx_ascii.parse(SAMPLE)

    def test_an_id_that_fits_int32_is_still_int64(self):
        """An id slot is always int64 regardless of magnitude: Blender's importer gates the
        object node and connection checks on the type character, not the value, so a small
        id typed by magnitude alone would read as a connection or object node the importer
        silently drops."""
        geometry = find(self.elements, "Geometry")
        self.assertEqual(geometry.props[0], 140718)
        self.assertEqual(geometry.props_type[0], "L")

    def test_an_id_too_large_for_int32_is_int64(self):
        model = find(self.elements, "Model")
        self.assertEqual(model.props[0], 1852219572480)
        self.assertEqual(model.props_type[0], "L")

    def test_a_connection_row_types_each_value_on_its_own(self):
        connection = find(self.elements, "C")
        self.assertEqual(connection.props, ["OO", 1852219572480, 0])
        self.assertEqual(connection.props_type, "SLL")

    def test_an_integer_outside_an_id_slot_still_types_by_magnitude(self):
        """The id slot table only overrides the slots it lists; everything else still types
        by magnitude, so the fallback is not lost."""
        layer = find(self.elements, "LayerElementUV")
        self.assertEqual(layer.props, [0])
        self.assertEqual(layer.props_type, "I")

    def test_a_large_integer_outside_an_id_slot_still_types_by_magnitude(self):
        elements, _ = fbx_ascii.parse(
            "Objects:  {\n\tGeometry: 1, \"\", \"\" {\n\t\tBigNumber: 1852219572480\n\t}\n}\n"
        )
        big_number = find(elements, "BigNumber")
        self.assertEqual(big_number.props, [1852219572480])
        self.assertEqual(big_number.props_type, "L")

    def test_a_bare_integer_leaf_is_int32(self):
        self.assertEqual(find(self.elements, "FBXVersion").props_type, "I")

    def test_a_node_with_no_properties_has_none(self):
        properties = find(self.elements, "Properties70")
        self.assertEqual(properties.props, [])
        self.assertEqual(properties.props_type, "")


class TestCharLeaves(unittest.TestCase):
    """A Model's "Shading" flag, seen in every real ASCII file Synty shipped: a bare letter
    outside Properties70 rather than a number, which is the FBX SDK's one byte char property.
    Binary carries the same value as a one byte "C" property holding the letter itself, so
    the parser must carry the letter through as a byte rather than a Python bool."""

    def test_a_bare_t_is_the_byte_t(self):
        elements, _ = fbx_ascii.parse("Objects:  {\n\tModel: 1, \"\", \"\" {\n\t\tShading: T\n\t}\n}\n")
        shading = find(elements, "Shading")
        self.assertEqual(shading.props, [b"T"])
        self.assertEqual(shading.props_type, "C")

    def test_a_bare_y_is_the_byte_y(self):
        elements, _ = fbx_ascii.parse("Objects:  {\n\tModel: 1, \"\", \"\" {\n\t\tShading: Y\n\t}\n}\n")
        shading = find(elements, "Shading")
        self.assertEqual(shading.props, [b"Y"])
        self.assertEqual(shading.props_type, "C")

    def test_a_bare_f_is_the_byte_f(self):
        elements, _ = fbx_ascii.parse("Objects:  {\n\tModel: 1, \"\", \"\" {\n\t\tShading: F\n\t}\n}\n")
        shading = find(elements, "Shading")
        self.assertEqual(shading.props, [b"F"])
        self.assertEqual(shading.props_type, "C")

    def test_a_bare_n_is_the_byte_n(self):
        elements, _ = fbx_ascii.parse("Objects:  {\n\tModel: 1, \"\", \"\" {\n\t\tShading: N\n\t}\n}\n")
        shading = find(elements, "Shading")
        self.assertEqual(shading.props, [b"N"])
        self.assertEqual(shading.props_type, "C")


# What Python type and shape each type character carries. This is the module's actual
# contract: a later task's encode_bin builder trusts this pairing without checking it again,
# and dispatches on the character alone (add_char asserts bytes of length 1, and so on).
TYPE_INVARIANTS = {
    "S": lambda value: isinstance(value, str),
    "I": lambda value: isinstance(value, int),
    "L": lambda value: isinstance(value, int),
    "Y": lambda value: isinstance(value, int),
    "D": lambda value: isinstance(value, float),
    "C": lambda value: isinstance(value, bytes) and len(value) == 1,
    "R": lambda value: isinstance(value, bytes),
    "d": lambda value: isinstance(value, list) and all(isinstance(item, float) for item in value),
    "i": lambda value: isinstance(value, list) and all(isinstance(item, int) for item in value),
}


class TestTypeInvariant(unittest.TestCase):
    """Every property's Python type must match the character it is tagged with. No other
    test asserts this directly, and it is the one thing the next task, which hands the tree
    to Blender's encode_bin writer, depends on completely."""

    def setUp(self):
        self.elements, _ = fbx_ascii.parse(SAMPLE)

    def test_every_property_matches_its_type_character(self):
        found = set()
        self._check(self.elements, found)
        # "R", raw bytes, is the one character no code path in this module assigns: none of
        # the 21 real ASCII files Synty shipped carries a property that would need it, and
        # this module invents nothing it cannot point at a real file for. Every other
        # character in the contract is exercised by SAMPLE.
        self.assertEqual(found, set(TYPE_INVARIANTS) - {"R"})

    def _check(self, elements, found):
        for element in elements:
            for value, character in zip(element.props, element.props_type):
                invariant = TYPE_INVARIANTS.get(character)
                self.assertIsNotNone(invariant, f"no invariant recorded for type {character!r}")
                self.assertTrue(invariant(value),
                                 f"{element.id}: {value!r} does not match type {character!r}")
                found.add(character)
            self._check(element.elems, found)


class TestPropertyRows(unittest.TestCase):
    def setUp(self):
        elements, _ = fbx_ascii.parse(SAMPLE)
        self.rows = find(elements, "Properties70").elems

    def test_a_declared_double_written_without_a_point_is_float64(self):
        self.assertEqual(self.rows[0].props_type, "SSSSD")
        self.assertEqual(self.rows[0].props[4], 1.0)
        self.assertIsInstance(self.rows[0].props[4], float)

    def test_a_row_carries_as_many_values_as_it_states(self):
        self.assertEqual(self.rows[1].props_type, "SSSSYYY")
        self.assertEqual(self.rows[1].props[4:], [5, 5, 5])

    def test_a_compound_declares_no_value(self):
        self.assertEqual(self.rows[2].props_type, "SSSS")
        # A row that names a no-value type but carries one anyway would silently succeed if
        # the distinction were not honored, falling back to reading the extra token by
        # syntax exactly as an unrecognized type does.
        with self.assertRaises(fbx_ascii.ParseError):
            fbx_ascii.parse(
                'Objects:  {\n\tModel: 1, "", "" {\n\t\tProperties70:  {\n'
                '\t\t\tP: "Original", "Compound", "", "",1\n\t\t}\n\t}\n}\n'
            )

    def test_a_string_row_is_a_string(self):
        self.assertEqual(self.rows[3].props_type, "SSSSS")

    def test_an_unknown_declared_type_falls_back_to_the_syntax(self):
        self.assertEqual(self.rows[4].props_type, "SSSSD")
        self.assertEqual(self.rows[4].props[4], 2.5)


class TestArrays(unittest.TestCase):
    def setUp(self):
        self.elements, _ = fbx_ascii.parse(SAMPLE)

    def test_an_array_wrapped_over_lines_is_read_whole(self):
        vertices = find(self.elements, "Vertices")
        self.assertEqual(vertices.props[0], [0.0, 0.0, 0.0, 1.0, 2.0, 3.0])

    def test_a_float_array_of_whole_numbers_is_still_float64(self):
        vertices = find(self.elements, "Vertices")
        self.assertEqual(vertices.props_type, "d")
        self.assertTrue(all(isinstance(value, float) for value in vertices.props[0]))

    def test_an_index_array_is_int32(self):
        indices = find(self.elements, "PolygonVertexIndex")
        self.assertEqual(indices.props_type, "i")
        self.assertEqual(indices.props[0], [0, 1, -3])

    def test_an_empty_array_is_empty_rather_than_absent(self):
        edges = find(self.elements, "Edges")
        self.assertEqual(edges.props, [[]])
        self.assertEqual(edges.props_type, "i")

    def test_an_unknown_array_key_fails_rather_than_guessing(self):
        text = "Objects:  {\n\tGeometry: 1, \"\", \"\" {\n\t\tWibble: *2 {\n\t\t\ta: 1,2\n\t\t}\n\t}\n}\n"
        with self.assertRaises(fbx_ascii.ParseError) as caught:
            fbx_ascii.parse(text)
        self.assertIn("Wibble", str(caught.exception))

    def test_a_declared_count_that_disagrees_fails(self):
        text = "Objects:  {\n\tGeometry: 1, \"\", \"\" {\n\t\tVertices: *4 {\n\t\t\ta: 1,2,3\n\t\t}\n\t}\n}\n"
        with self.assertRaises(fbx_ascii.ParseError) as caught:
            fbx_ascii.parse(text)
        self.assertIn("4", str(caught.exception))


class TestNameAndClass(unittest.TestCase):
    """Binary stores name, separator, class. ASCII writes class, ::, name."""

    def setUp(self):
        self.elements, _ = fbx_ascii.parse(SAMPLE)

    def test_an_object_name_is_reordered(self):
        self.assertEqual(find(self.elements, "Model").props[1],
                         "Light_Shaft_Base\x00\x01Model")

    def test_an_empty_name_keeps_its_class(self):
        self.assertEqual(find(self.elements, "Geometry").props[1], "\x00\x01Geometry")

    def test_scene_info_is_reordered(self):
        self.assertEqual(find(self.elements, "SceneInfo").props[0],
                         "GlobalInfo\x00\x01SceneInfo")

    def test_a_texture_reference_is_reordered(self):
        self.assertEqual(find(self.elements, "Media").props[0], "file1178\x00\x01Video")
        self.assertEqual(find(self.elements, "TextureName").props[0],
                         "file1178\x00\x01Texture")

    def test_an_ordinary_string_is_left_alone(self):
        self.assertEqual(find(self.elements, "Type").props[0], "UserData")
        self.assertEqual(find(self.elements, "Model").props[2], "LimbNode")


class TestMalformedInput(unittest.TestCase):
    def test_an_unclosed_block_fails(self):
        with self.assertRaises(fbx_ascii.ParseError):
            fbx_ascii.parse("Objects:  {\n\tGeometry: 1, \"\", \"\" {\n}\n")

    def test_an_unmatched_closing_brace_fails(self):
        with self.assertRaises(fbx_ascii.ParseError):
            fbx_ascii.parse("Objects:  {\n}\n}\n")

    def test_a_line_that_is_not_a_node_fails(self):
        with self.assertRaises(fbx_ascii.ParseError) as caught:
            fbx_ascii.parse("Objects:  {\n\tnonsense without a colon\n}\n")
        self.assertIn("nonsense", str(caught.exception))

    def test_a_file_with_no_version_reports_zero(self):
        _, version = fbx_ascii.parse("Objects:  {\n}\n")
        self.assertEqual(version, 0)


class TestGeometryCounts(unittest.TestCase):
    """What the text says the importer should build, for checking against what it built."""

    def test_counts_come_from_the_declared_arrays(self):
        elements, _ = fbx_ascii.parse(SAMPLE)
        self.assertEqual(fbx_ascii.geometry_counts(elements),
                         {"vertices": 2, "loops": 3, "uv_layers": 1})

    def test_several_meshes_are_summed(self):
        elements, _ = fbx_ascii.parse(SAMPLE + SAMPLE)
        self.assertEqual(fbx_ascii.geometry_counts(elements),
                         {"vertices": 4, "loops": 6, "uv_layers": 2})

    def test_a_file_with_no_geometry_counts_nothing(self):
        elements, _ = fbx_ascii.parse("Objects:  {\n}\n")
        self.assertEqual(fbx_ascii.geometry_counts(elements),
                         {"vertices": 0, "loops": 0, "uv_layers": 0})


class TestHeader(unittest.TestCase):
    def test_the_missing_header_nodes_are_added_after_the_extension(self):
        elements, _ = fbx_ascii.parse(SAMPLE)
        fbx_ascii.ensure_header(elements)
        self.assertEqual([element.id for element in elements],
                         ["FBXHeaderExtension", "FileId", "CreationTime", "Creator",
                          "Objects", "Connections"])

    def test_the_types_are_what_encode_bin_asserts(self):
        elements, _ = fbx_ascii.parse(SAMPLE)
        fbx_ascii.ensure_header(elements)
        self.assertEqual(find(elements, "FileId").props_type, "R")
        self.assertIsInstance(find(elements, "FileId").props[0], bytes)
        self.assertEqual(find(elements, "CreationTime").props_type, "S")
        self.assertIsInstance(find(elements, "CreationTime").props[0], str)
        self.assertEqual(find(elements, "Creator").props_type, "S")

    def test_a_node_that_is_already_there_is_left_alone(self):
        elements, _ = fbx_ascii.parse(SAMPLE + 'Creator: "FBX SDK/FBX Plugins"\n')
        fbx_ascii.ensure_header(elements)
        self.assertEqual([element.id for element in elements].count("Creator"), 1)
        self.assertEqual(find(elements, "Creator").props[0], "FBX SDK/FBX Plugins")


if __name__ == "__main__":
    unittest.main()

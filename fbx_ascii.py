"""Read the ASCII serialization of FBX into the node tree the binary reader produces.

Blender's importer reads binary FBX only, and Synty ships a handful of models in the ASCII
variant by mistake. The two are the same tree of named nodes carrying the same typed
properties, so a model in the wrong serialization is recoverable without touching the model:
parse the text here, then write the tree back out as binary with Blender's own encode_bin
and hand that to the importer. See docs/DESIGN.md.

Pure Python by design. The Blender worker imports this module, but it must never import bpy,
so that every typing rule below is covered by the test suite under plain CPython.
"""

from __future__ import annotations

import collections
import re

# The same fields as parse_fbx.FBXElem, which is what the binary reader returns, so the two
# trees read alike. Text is str here rather than bytes: this is a text parser, and the
# encode_bin builder encodes at the boundary where the writer needs bytes.
Element = collections.namedtuple("Element", ("id", "props", "props_type", "elems"))

BINARY_MAGIC = b"Kaydara FBX Binary"

INT32_RANGE = range(-2 ** 31, 2 ** 31)

# An array's element type, which the text never states. Inferring it from the values is not
# safe: a float array whose values are all whole numbers reads exactly like an integer one,
# and Vertices read as int32 is geometry that is garbage rather than an error. So the type
# comes from the key, and a key that is not here fails the file rather than being guessed at.
# These sixteen are every array key in the ASCII models Synty shipped.
ARRAY_TYPES = {
    "Vertices": "d", "Normals": "d", "NormalsW": "d", "UV": "d", "Colors": "d",
    "Weights": "d", "Matrix": "d", "Transform": "d", "TransformLink": "d",
    "PolygonVertexIndex": "i", "Edges": "i", "UVIndex": "i", "Materials": "i",
    "Smoothing": "i", "ColorIndex": "i", "Indexes": "i",
}

# The element type a Properties70 row gives its values, measured off 179 binary FBX in the
# three packs that ship ASCII rather than assumed. A row carries this character once per
# value and states its own count, so "Short" appears as both Y and YYY. A type not listed
# falls back to reading the syntax, which is safe in a way that guessing an array is not:
# these are scalars, and the only reader downstream is Blender's importer, which holds them
# as ordinary Python numbers.
PROPERTY_TYPES = {
    "Bool": "I", "bool": "I", "int": "I", "Integer": "I", "enum": "I",
    "Visibility Inheritance": "I", "Short": "Y", "KTime": "L", "ULongLong": "L",
    "double": "D", "Number": "D", "Visibility": "D", "Color": "D", "ColorRGB": "D",
    "Vector": "D", "Vector3D": "D", "Lcl Translation": "D", "Lcl Rotation": "D",
    "Lcl Scaling": "D", "KString": "S", "DateTime": "S",
}

# Declared types that carry no value at all, which is different from a declared type this
# module does not recognize: PROPERTY_TYPES has no entry for either, but a row naming one of
# these is complete at the fourth token, while a row naming an unrecognized type still reads
# whatever comes after by syntax.
NO_VALUE_TYPES = frozenset({"Compound", "object"})

# A handful of leaves outside Properties70, such as a Model's "Shading" flag, carry a bare
# unquoted letter instead of a number or a string. No other legal ASCII token is a single
# letter, so this is not read from context: it is the FBX SDK's one-byte char property,
# which different exporter versions write as either letter pair for the same true or false
# meaning. Binary FBX carries the same value as a one byte "C" property holding the letter
# itself, so the letter is carried through as a byte rather than translated into a bool.
CHAR_LETTERS = frozenset({"Y", "T", "N", "F"})

# Array values wrap across lines at about 2048 characters, 5535 times over the ASCII models
# Synty shipped, so each array is folded onto its opening line before anything else reads the
# text. An array body holds numbers, commas and whitespace only, so matching to the next brace
# cannot run past the end of one.
ARRAY_BLOCK = re.compile(r"\*(\d+)\s*\{\s*(?:a:)?([^}]*)\}")

ARRAY_NODE = re.compile(r"^([A-Za-z0-9_]+):\s*\*(\d+)\s*\{a:(.*)\}$")
OPEN_NODE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*?)\s*\{$")
LEAF_NODE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")


class ParseError(Exception):
    """The text is not ASCII FBX, or says something this module refuses to guess at."""


def is_binary(data: bytes) -> bool:
    """True when a file's opening bytes are the binary FBX magic."""
    return data.startswith(BINARY_MAGIC)


def parse(text: str) -> tuple[list[Element], int]:
    """Read ASCII FBX text into top level elements and the FBX version it declares."""
    elements = []
    stack = [elements]
    for line in _lines(text):
        if line == "}":
            if len(stack) == 1:
                raise ParseError("unmatched closing brace")
            stack.pop()
            continue
        match = ARRAY_NODE.match(line)
        if match:
            stack[-1].append(_array(match.group(1), int(match.group(2)), match.group(3)))
            continue
        match = OPEN_NODE.match(line)
        if match:
            element = _node(match.group(1), match.group(2))
            stack[-1].append(element)
            stack.append(element.elems)
            continue
        match = LEAF_NODE.match(line)
        if not match:
            raise ParseError(f"cannot read {line!r}")
        stack[-1].append(_node(match.group(1), match.group(2)))
    if len(stack) != 1:
        raise ParseError("a block was never closed")
    _postprocess(elements, None)
    return elements, _version(elements)


def _lines(text):
    """Every meaningful line, comments removed and wrapped arrays folded back together."""
    text = "\n".join(_uncommented(line) for line in text.splitlines())
    text = ARRAY_BLOCK.sub(lambda match: "*%s {a:%s}" % (match.group(1),
                                                         "".join(match.group(2).split())), text)
    return [line for line in (raw.strip() for raw in text.splitlines()) if line]


def _uncommented(line):
    """A line with any comment removed. A semicolon inside a string is not a comment."""
    inside = False
    for index, character in enumerate(line):
        if character == '"':
            inside = not inside
        elif character == ";" and not inside:
            return line[:index]
    return line


def _node(name, text):
    """One non-array node, its properties typed."""
    tokens = _split(text)
    if name == "P" and len(tokens) >= 4:
        return _property_row(tokens)
    props, types = [], []
    for token in tokens:
        value, character = _scalar(token)
        props.append(value)
        types.append(character)
    return Element(name, props, "".join(types), [])


def _property_row(tokens):
    """A Properties70 row, whose values are typed by the type name the row itself declares."""
    props, types = [], []
    for token in tokens[:4]:
        value, character = _scalar(token)
        props.append(value)
        types.append(character)
    declared_type = props[1]
    if declared_type in NO_VALUE_TYPES:
        if len(tokens) > 4:
            raise ParseError(f"{declared_type!r} declares no value but the row carries one")
        return Element("P", props, "".join(types), [])
    declared_character = PROPERTY_TYPES.get(declared_type)
    for token in tokens[4:]:
        value, character = _typed(token, declared_character) if declared_character else _scalar(token)
        props.append(value)
        types.append(character)
    return Element("P", props, "".join(types), [])


def _array(name, count, body):
    """One array node. Its element type comes from its key, never from its values."""
    character = ARRAY_TYPES.get(name)
    if character is None:
        raise ParseError(f"{name}: no element type is known for this array key")
    values = body.split(",") if body else []
    if len(values) != count:
        raise ParseError(f"{name}: declares {count} values and holds {len(values)}")
    convert = float if character == "d" else int
    return Element(name, [[convert(value) for value in values]], character, [])


def _split(text):
    """Property tokens, split on the commas that are not inside a string."""
    if not text.strip():
        return []
    tokens, current, inside = [], "", False
    for character in text:
        if character == '"':
            inside = not inside
        elif character == "," and not inside:
            tokens.append(current)
            current = ""
            continue
        current += character
    tokens.append(current)
    return [token.strip() for token in tokens]


def _scalar(token):
    """One property value typed by how it is written, for where nothing declares its type."""
    if token.startswith('"'):
        return _string(token), "S"
    if token in CHAR_LETTERS:
        return token.encode("ascii"), "C"
    if any(character in token for character in ".eE"):
        return float(token), "D"
    try:
        value = int(token)
    except ValueError:
        raise ParseError(f"cannot read the value {token!r}") from None
    return value, "I" if value in INT32_RANGE else "L"


def _typed(token, character):
    """One property value typed by what its row declared."""
    if token.startswith('"'):
        return _string(token), "S"
    if character == "S":
        return _unescape(token), "S"
    if character == "D":
        return float(token), "D"
    if character in ("I", "L", "Y"):
        return int(token), character
    return _scalar(token)


def _string(token):
    """The text of a quoted token, delimiters removed and any escaped quote restored."""
    return _unescape(token[1:-1])


def _unescape(text):
    """&quot; is how the format escapes an embedded quote."""
    return text.replace("&quot;", '"')


# Where a node stores an object's name and its class together. Binary writes the name, the
# separator and then the class; ASCII writes the class, "::" and then the name, so the two
# halves are reversed. Blender's own json2fbx converts these with a blanket textual replace,
# which keeps the ASCII order and so names every object after its class and classes every
# object after its name, silently. The slots below were read off a real binary Synty FBX.
def _name_slot(parent, element):
    """The index of the property carrying a name and class pair, or None."""
    if parent == "Objects":
        return 1
    if parent == "FBXHeaderExtension" and element.id == "SceneInfo":
        return 0
    if parent == "Texture" and element.id in ("Media", "TextureName"):
        return 0
    return None


def _name_first(text):
    """Reorder "Class::Name" into the binary form, which is name, separator, class."""
    if not isinstance(text, str) or "::" not in text:
        return text
    class_name, _, name = text.partition("::")
    return name + "\x00\x01" + class_name


# Where a node stores a 64 bit object id, regardless of how small the number is. Binary's
# importer gates on the type character rather than the value: elem_uuid asserts int64, an
# object node's own id must read "LSS", and a connection is skipped, silently, unless both
# endpoints read "L". A scene root connection names id 0, which fits int32, so typing by
# magnitude alone reads the wrong character for it. Measured off 199 binary FBX in four packs.
def _id_slots(parent, element):
    """The property slots this node carries as a 64 bit object id, or ()."""
    if parent == "Objects":
        return (0,)
    if parent == "Connections" and element.id == "C":
        return (1, 2)
    if parent == "Documents" and element.id == "Document":
        return (0,)
    if parent == "Document" and element.id == "RootNode":
        return (0,)
    if parent == "PoseNode" and element.id == "Node":
        return (0,)
    if parent == "Take" and element.id in ("LocalTime", "ReferenceTime"):
        return (0, 1)
    return ()


# Where a node stores a float64 written as a whole number, which magnitude typing would
# otherwise read as an integer. Nothing in the importer reads these three today, but the
# tree should be the tree the binary reader produces. Measured off the same 199 files.
def _float_slots(parent, element):
    """The property slots this node carries as a float64, or ()."""
    if parent == "Texture" and element.id in ("ModelUVScaling", "ModelUVTranslation"):
        return (0, 1)
    if parent == "Deformer" and element.id == "Link_DeformAcuracy":
        return (0,)
    if parent == "AnimationCurve" and element.id == "Default":
        return (0,)
    return ()


def _retyped(element, parent):
    """Element with its id and float slots retyped by position, since the grammar cannot
    tell either apart from a plain integer by looking at one token alone."""
    types = list(element.props_type)
    changed = False
    for slot in _id_slots(parent, element):
        if slot < len(types) and types[slot] == "I":
            types[slot] = "L"
            changed = True
    for slot in _float_slots(parent, element):
        if slot < len(types) and types[slot] == "I":
            element.props[slot] = float(element.props[slot])
            types[slot] = "D"
            changed = True
    return element._replace(props_type="".join(types)) if changed else element


def _postprocess(elements, parent):
    """Walk the tree once: reorder each object's name and class, and retype the id and float
    slots that position, rather than the token's own syntax, determines."""
    for index, element in enumerate(elements):
        name_index = _name_slot(parent, element)
        if name_index is not None and len(element.props) > name_index:
            element.props[name_index] = _name_first(element.props[name_index])
        element = _retyped(element, parent)
        elements[index] = element
        _postprocess(element.elems, element.id)


def _version(elements):
    """The FBX version the header declares, or 0 when it declares none."""
    for element in elements:
        if element.id == "FBXVersion" and element.props:
            return int(element.props[0])
        found = _version(element.elems)
        if found:
            return found
    return 0

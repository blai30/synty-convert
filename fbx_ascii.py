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
# value and states its own count, so "Short" appears as both Y and YYY. Compound and object
# declare no value at all. A type not listed falls back to reading the syntax, which is safe
# in a way that guessing an array is not: these are scalars, and the only reader downstream
# is Blender's importer, which holds them as ordinary Python numbers.
PROPERTY_TYPES = {
    "Bool": "I", "bool": "I", "int": "I", "Integer": "I", "enum": "I",
    "Visibility Inheritance": "I", "Short": "Y", "KTime": "L", "ULongLong": "L",
    "double": "D", "Number": "D", "Visibility": "D", "Color": "D", "ColorRGB": "D",
    "Vector": "D", "Vector3D": "D", "Lcl Translation": "D", "Lcl Rotation": "D",
    "Lcl Scaling": "D", "KString": "S", "DateTime": "S", "Compound": "", "object": "",
}

# A handful of leaves outside Properties70, such as a Model's "Shading" flag, carry a bare
# unquoted letter instead of a number or a string. No other legal ASCII token is a single
# letter, so this is not read from context: it is the FBX SDK's one-byte boolean, which
# different exporter versions write as either letter pair for the same true or false. Binary
# FBX carries the same value as a one byte "C" property.
BOOLEAN_LETTERS = {"Y": True, "T": True, "N": False, "F": False}

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


def is_binary(data):
    """True when a file's opening bytes are the binary FBX magic."""
    return data.startswith(BINARY_MAGIC)


def parse(text):
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
    _encode_names(elements, None)
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
    declared = PROPERTY_TYPES.get(props[1])
    for token in tokens[4:]:
        value, character = _typed(token, declared) if declared else _scalar(token)
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
    if token in BOOLEAN_LETTERS:
        return BOOLEAN_LETTERS[token], "C"
    if any(character in token for character in ".eE"):
        return float(token), "D"
    try:
        value = int(token)
    except ValueError:
        raise ParseError(f"cannot read the value {token!r}")
    return value, "I" if value in INT32_RANGE else "L"


def _typed(token, character):
    """One property value typed by what its row declared."""
    if token.startswith('"'):
        return _string(token), "S"
    if character == "S":
        return _string(f'"{token}"'), "S"
    if character == "D":
        return float(token), "D"
    if character in ("I", "L", "Y"):
        return int(token), character
    return _scalar(token)


def _string(token):
    """The text of a quoted token. &quot; is how the format escapes an embedded quote."""
    return token.strip('"').replace("&quot;", '"')


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


def _encode_names(elements, parent):
    for element in elements:
        index = _name_slot(parent, element)
        if index is not None and len(element.props) > index:
            element.props[index] = _name_first(element.props[index])
        _encode_names(element.elems, element.id)


def _name_first(text):
    """Reorder "Class::Name" into the binary form, which is name, separator, class."""
    if not isinstance(text, str) or "::" not in text:
        return text
    class_name, _, name = text.partition("::")
    return name + "\x00\x01" + class_name


def _version(elements):
    """The FBX version the header declares, or 0 when it declares none."""
    for element in elements:
        if element.id == "FBXVersion" and element.props:
            return int(element.props[0])
        found = _version(element.elems)
        if found:
            return found
    return 0

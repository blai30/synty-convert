"""Match a Synty FBX texture reference to a file the pack actually ships.

Synty's FBX materials point at internal authoring files (``.psd``, ``.tif``) that are
usually named for a different pack than the one shipping them, so references almost never
resolve directly. ``PolygonMilitary_Texture_01_A.psd`` is how the BattleRoyale pack refers
to its own ``PolygonBattleRoyale_Texture_01_A.png``.

What survives across the rename is the distinctive tail of the name, so matching scores
candidates on their trailing token run, backed by overall token overlap. Anything that is
not a confident, unambiguous winner is left unresolved rather than guessed at.

Pure Python: this module is imported both by the Blender worker and by the CLI.
"""

from __future__ import annotations

import collections
import os
import re
from dataclasses import dataclass

TEXTURE_SUFFIXES = {".png", ".tga", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".dds", ".psd", ".exr"}

# Authoring cruft that carries no identity.
NOISE_TOKENS = {"cleaned", "master", "final", "copy", "bak", "backup", "wip", "temp", "source", "new", "old"}

# Tokens that distinguish a variant from its base map. A reference asking for the plain
# atlas must not silently resolve to the damaged or normal-map version of it.
VARIANT_TOKENS = {"damaged", "alt", "emission", "normal", "metallic", "specular", "mask", "alpha", "roughness"}

# Different words for the same variant across Synty's authoring and shipping names.
SYNONYMS = {"destroyed": "damaged", "broken": "damaged", "wrecked": "damaged",
            "emmision": "emission", "emissive": "emission", "spec": "specular",
            "rough": "roughness", "norm": "normal", "nrm": "normal"}

MIN_SCORE = 8.0
MIN_MARGIN = 3.0

# Separates a pack name from a path suffix in an override value, for when the texture a
# pack asks for is one another pack ships. Synty's biome packs are built on the base Nature
# pack and reference its atlas directly, so the file genuinely lives next door.
FOREIGN_SEPARATOR = "::"


@dataclass
class Match:
    path: str
    score: float
    method: str


def tokenize(stem):
    """Split a texture stem into comparable tokens.

    Lowercases, splits on separators and letter/digit boundaries, normalizes plurals,
    strips leading zeros from numbers, applies variant synonyms and drops noise.
    """
    parts = re.split(r"[^A-Za-z0-9]+", stem)
    tokens = []
    for part in parts:
        for piece in re.findall(r"[A-Za-z]+|[0-9]+", part):
            piece = piece.lower()
            if piece.isdigit():
                tokens.append(str(int(piece)))
                continue
            piece = SYNONYMS.get(piece, piece)
            # Plural and singular spellings of the same word should compare equal.
            if len(piece) > 3 and piece.endswith("s") and not piece.endswith("ss"):
                piece = piece[:-1]
            if piece not in NOISE_TOKENS:
                tokens.append(piece)
    return tokens


def trailing_run(reference, candidate):
    """How many tokens the two names share at the end."""
    count = 0
    for left, right in zip(reversed(reference), reversed(candidate)):
        if left != right:
            break
        count += 1
    return count


def score_candidate(reference, candidate):
    """Score a candidate's tokens against the reference's. Higher is better."""
    if not reference or not candidate:
        return 0.0
    # Synty puts the variant qualifier at either end (Vehicle_Destroyed_01 against
    # Vehicles_01_Damaged), so the descriptive part is compared positionally while the
    # qualifier itself is compared as a set.
    core_reference = [token for token in reference if token not in VARIANT_TOKENS]
    core_candidate = [token for token in candidate if token not in VARIANT_TOKENS]
    run = trailing_run(core_reference, core_candidate)
    overlap = len(set(reference) & set(candidate)) / len(set(reference) | set(candidate))
    score = run * 10.0 + overlap * 5.0
    # A variant qualifier on one side but not the other means these are different maps.
    if (VARIANT_TOKENS & set(reference)) != (VARIANT_TOKENS & set(candidate)):
        score -= 25.0
    return score


def index_textures(root):
    """Every texture file under a pack, as absolute paths."""
    found = []
    for directory, _, names in os.walk(root):
        for name in names:
            if os.path.splitext(name)[1].lower() in TEXTURE_SUFFIXES:
                found.append(os.path.join(directory, name))
    return sorted(found)


def tail_variants(tokens, max_trim=3):
    """The token list, then progressively shorter versions of it.

    Synty's references often carry an artist's working suffix that never shipped, as in
    PolygonCastle_Texture_01_A_Jason against PolygonCastle_Texture_01_A. Trimming from the
    end recovers those; a trimmed candidate still has to clear the normal confidence bar.
    """
    yield tokens
    for trimmed in range(1, max_trim + 1):
        if len(tokens) - trimmed >= 2:
            yield tokens[:len(tokens) - trimmed]


def best_match(reference_tokens, candidates, common, min_run=1):
    """Highest scoring candidate, or None if it is weak, contested or uninformative."""
    ranked = sorted(((score_candidate(reference_tokens, tokens), name, tokens)
                     for name, tokens in candidates.items()),
                    key=lambda entry: (-entry[0], entry[1]))
    best_score, best_name, best_tokens = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if best_score < MIN_SCORE or best_score - runner_up < MIN_MARGIN:
        return None
    core_reference = [token for token in reference_tokens if token not in VARIANT_TOKENS]
    core_candidate = [token for token in best_tokens if token not in VARIANT_TOKENS]
    length = trailing_run(core_reference, core_candidate)
    if length < min_run:
        return None
    # A run made only of tokens shared by most of the pack (texture, 01, a) says nothing:
    # it is how Dungeons_Texture would otherwise capture Track_Texture.
    run = core_reference[len(core_reference) - length:]
    if run and all(token in common for token in run):
        return None
    return best_name, round(best_score, 2)


def resolve(reference, textures, overrides=None, foreign=None):
    """Resolve one FBX texture reference against a pack's shipped textures.

    ``foreign`` maps a pack name to that pack's texture index, for override values written
    as ``OtherPack::path/suffix.png``. Returns a ``Match``, or None when nothing is
    confident enough to be worth using.
    """
    if not reference or not textures:
        return None
    stem = os.path.splitext(os.path.basename(reference.replace("\\", "/")))[0]

    lookup = {os.path.splitext(os.path.basename(path))[0].lower(): path for path in textures}
    if overrides:
        target = overrides.get(stem) or overrides.get(stem.lower())
        if target:
            pool = textures
            if FOREIGN_SEPARATOR in target:
                pack, target = target.split(FOREIGN_SEPARATOR, 1)
                pool = (foreign or {}).get(pack, [])
            for path in pool:
                if path.replace("\\", "/").endswith(target.replace("\\", "/")):
                    return Match(path, 100.0, "override")
            return None

    # Same name, different extension: the .psd was simply exported as a .png.
    if stem.lower() in lookup:
        return Match(lookup[stem.lower()], 100.0, "exact")

    reference_tokens = tokenize(stem)
    if not reference_tokens:
        return None

    candidates = {name: tokenize(name) for name in lookup}
    normalized = {"_".join(tokens): name for name, tokens in candidates.items()}
    frequency = collections.Counter(token for tokens in candidates.values() for token in set(tokens))
    common = {token for token, count in frequency.items() if count > max(1, len(candidates) // 2)}

    for depth, tokens in enumerate(tail_variants(reference_tokens)):
        method = "normalized" if depth == 0 else "trimmed"
        name = normalized.get("_".join(tokens))
        if name:
            return Match(lookup[name], 100.0 - depth, method)
        # Trimming has already thrown away tokens that might have been the distinguishing
        # ones, so one shared trailing token is no longer evidence. It is how
        # PolygonNatureBiomes_Texture_01_Tom, shorn of its artist suffix, otherwise lands
        # on Birch_Trunk_Texture: the only candidate ending in "texture".
        found = best_match(tokens, candidates, common, min_run=1 if depth == 0 else 2)
        if found:
            return Match(lookup[found[0]], found[1], "tokens" if depth == 0 else "trimmed")
    return None

"""Drift-proof design-token tests.

These encode the frontend-design skill's mechanical rules as CI:
1. No raw color literal outside :root — every color in a component rule must
   reference a token (pure black/white shadow alphas exempt: shadows are
   depth, not semantic color).
2. Spacing (padding/gap) values sit on the 4px grid (2px/6px half-steps
   allowed at the small end).
3. Every var(--x) consumed anywhere is either defined in :root or one of the
   per-instance vars components set via inline style at render time.
"""

from __future__ import annotations

import re

from homelab_guardian.web import _AUTH_STYLE, _INK, PAGE_STYLE

# Vars injected per-instance through style="--x:..." at render time.
_RUNTIME_VARS = {"glow", "oc", "ocbg", "chbg", "chtx", "gd", "cbg", "ctx", "nb"}

# Shadow/highlight alphas: depth recipe, not semantic color.
_EXEMPT_COLOR = re.compile(r"rgba\(\s*(0,\s*0,\s*0|255,\s*255,\s*255)\s*,")


def _root_and_rest(css: str) -> tuple[str, str]:
    match = re.search(r":root\s*\{[^}]*\}", css)
    assert match, "PAGE_STYLE must contain a :root block"
    return match.group(0), css[: match.start()] + css[match.end():]


def test_no_raw_colors_outside_root() -> None:
    _, rest = _root_and_rest(PAGE_STYLE)
    rest += _AUTH_STYLE
    offenders = []
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", rest):
        literal = m.group(0)
        if _EXEMPT_COLOR.match(literal):
            continue
        line = rest[: m.start()].rsplit("\n", 1)[-1] + literal
        offenders.append(line.strip())
    assert not offenders, f"raw colors outside :root: {offenders}"


def test_ink_map_uses_only_tokens() -> None:
    for status, ink in _INK.items():
        for key in ("tx", "solid", "bg", "glow"):
            value = ink[key]
            assert "var(--" in value, f"_INK[{status}][{key}] must derive from a token: {value}"
            assert not re.search(r"#[0-9a-fA-F]{3,8}|rgba?\(\s*\d", value), (
                f"_INK[{status}][{key}] carries a raw color: {value}"
            )


def test_spacing_on_grid() -> None:
    _, rest = _root_and_rest(PAGE_STYLE)
    rest += _AUTH_STYLE
    offenders = []
    for m in re.finditer(r"(?:^|;|\{)\s*((?:padding|gap|column-gap|row-gap)(?:-[a-z]+)?)\s*:\s*([^;}]+)", rest):
        prop, value = m.groups()
        # calc = deliberate optical math; strip it (handles one nesting level)
        value = re.sub(r"calc\((?:[^()]|\([^()]*\))*\)", "", value)
        for px in re.findall(r"(\d+(?:\.\d+)?)px", value):
            number = float(px)
            if number % 4 != 0 and number not in (2.0, 6.0):
                offenders.append(f"{prop}: {value.strip()} ({px}px off-grid)")
    assert not offenders, f"spacing off the 4px grid: {offenders}"


def test_every_var_reference_is_defined() -> None:
    root, _ = _root_and_rest(PAGE_STYLE)
    defined = set(re.findall(r"--([\w-]+)\s*:", root))
    consumed = set(re.findall(r"var\(--([\w-]+)", PAGE_STYLE + _AUTH_STYLE))
    for ink in _INK.values():
        consumed.update(re.findall(r"var\(--([\w-]+)", " ".join(ink.values())))
    missing = consumed - defined - _RUNTIME_VARS
    assert not missing, f"var() references with no :root definition: {sorted(missing)}"

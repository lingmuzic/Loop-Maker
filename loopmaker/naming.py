"""Output file naming templates.

A template is plain text with tokens in braces::

    "AMB_{name}_{index}_loop"  ->  AMB_Desert_Wind_01_loop.wav

Tokens:
    {name}      input file name without extension (illegal characters -> "_")
    {category}  free text from the Category field (e.g. "AMB")
    {index}     1-based position in the batch, zero-padded (01, 02, ...)
    {suffix}    free text from the Suffix field (default "loop")

Variations always get ``_v01``, ``_v02`` ... appended to the rendered name,
so they work with any template. The extension comes from the output format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKENS = ("name", "category", "index", "suffix")
DEFAULT_TEMPLATE = "{name}_{suffix}"
DEFAULT_SUFFIX = "loop"
DEFAULT_CATEGORY = "AMB"

# Characters Windows forbids in file names (the strictest common platform).
ILLEGAL_CHARS = '<>:"/\\|?*'
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
MAX_NAME_LENGTH = 200  # leaves room for the folder path within Windows' 260-char limit


def _illegal_in(text: str) -> list[str]:
    found = sorted({c for c in text if c in ILLEGAL_CHARS or ord(c) < 32})
    return [repr(c) if ord(c) < 32 else c for c in found]


def check_free_text(label: str, text: str) -> list[str]:
    """Problems with user text that gets inserted into names (category/suffix)."""
    bad = _illegal_in(text)
    return [f"{label} contains characters not allowed in file names: {' '.join(bad)}"] if bad else []


def template_errors(template: str) -> list[str]:
    """Validate a template. Returns human-readable problems (empty = valid)."""
    if not template.strip():
        return ["template is empty"]
    problems = []
    for token in _TOKEN_RE.findall(template):
        if token not in TOKENS:
            problems.append(f"unknown token {{{token}}}; allowed: " + ", ".join(f"{{{t}}}" for t in TOKENS))
    literal = _TOKEN_RE.sub("", template)
    if "{" in literal or "}" in literal:
        problems.append("unbalanced '{' or '}'")
    bad = _illegal_in(literal.replace("{", "").replace("}", ""))
    if bad:
        problems.append(f"template contains characters not allowed in file names: {' '.join(bad)}")
    if template.rstrip() != template or template.endswith("."):
        problems.append("a file name cannot end with a space or a dot")
    return problems


def sanitize(text: str) -> str:
    """Replace characters that are illegal in file names with '_'."""
    return "".join("_" if (c in ILLEGAL_CHARS or ord(c) < 32) else c for c in text)


@dataclass(frozen=True)
class Naming:
    """A validated template plus the free-text values it fills in."""

    template: str = DEFAULT_TEMPLATE
    category: str = DEFAULT_CATEGORY
    suffix: str = DEFAULT_SUFFIX

    def errors(self) -> list[str]:
        return (
            template_errors(self.template)
            + check_free_text("category", self.category)
            + check_free_text("suffix", self.suffix)
        )

    def warnings(self) -> list[str]:
        if not any(f"{{{t}}}" in self.template for t in ("name", "index")):
            return ["template has neither {name} nor {index}: batch outputs will collide and get _2, _3 ..."]
        return []

    def render(self, input_stem: str, index: int, batch_size: int = 1, variation: int | None = None) -> str:
        """Render the base name (no extension) for one output.

        ``index`` is 1-based; it is padded to at least two digits, more for
        batches of 100+ files so names still sort correctly.
        """
        width = max(2, len(str(batch_size)))
        values = {
            "name": sanitize(input_stem),
            "category": self.category,
            "index": str(index).zfill(width),
            "suffix": self.suffix,
        }
        name = _TOKEN_RE.sub(lambda m: values[m.group(1)], self.template)
        if variation is not None:
            name += f"_v{variation:02d}"
        problem = rendered_name_error(name)
        if problem:
            raise ValueError(problem)
        return name


def rendered_name_error(name: str) -> str | None:
    """Final checks on a rendered base name; None if it is usable."""
    if not name or not name.strip():
        return "the template renders to an empty file name"
    if name.split(".")[0].upper() in _RESERVED:
        return f"{name!r} is a reserved name on Windows"
    if name.endswith((" ", ".")):
        return f"{name!r} ends with a space or dot, which Windows does not allow"
    if len(name) > MAX_NAME_LENGTH:
        return f"rendered name is {len(name)} characters; keep it under {MAX_NAME_LENGTH}"
    return None

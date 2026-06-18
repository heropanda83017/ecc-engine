"""Load role-specific prompt templates."""

import os
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent.resolve()
_ROLE_MAP = {
    "arch": "ARCH.md",
    "review": "REVIEW.md",
    "engine": "ENGINE.md",
    "final-review": "FINAL-REVIEW.md",
    "spec-reviewer": "SPEC-REVIEWER.md",
    "code-quality-reviewer": "CODE-QUALITY-REVIEWER.md",
    "writing-plans": "WRITING-PLANS.md",
    "receiving-code-review": "RECEIVING-CODE-REVIEW.md",
    "pre-plan-validation": "PRE-PLAN-VALIDATION.md",
}


def load_prompt(role: str) -> str:
    """Load the prompt template for a given role.

    Args:
        role: Role name. One of 'arch', 'review', 'engine', 'final-review', 'writing-plans'.

    Returns:
        The prompt template text.

    Raises:
        ValueError: If the role is unknown.
        FileNotFoundError: If the template file is missing.
    """
    fname = _ROLE_MAP.get(role)
    if not fname:
        raise ValueError(f"Unknown role '{role}'. Options: {list(_ROLE_MAP.keys())}")
    path = _TEMPLATE_DIR / fname
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding='utf-8')


def list_roles():
    """Return all available role names."""
    return list(_ROLE_MAP.keys())


if __name__ == "__main__":
    import sys
    role = sys.argv[1] if len(sys.argv) > 1 else "arch"
    print(load_prompt(role))

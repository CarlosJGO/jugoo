"""Reusable visual primitives shared across shell modules."""

from .labels import shell_label
from .module import SHELL_MODULE_CLASS, ShellModule
from .tokens import SHELL_MODULE_INNER_SPACING, SHELL_MODULE_STACK_SPACING

__all__ = [
    "SHELL_MODULE_CLASS",
    "SHELL_MODULE_INNER_SPACING",
    "SHELL_MODULE_STACK_SPACING",
    "ShellModule",
    "shell_label",
]

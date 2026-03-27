"""Pre-execution script validation with AST-based security whitelist.

Walks the script's AST to reject dangerous constructs (imports, exec, eval,
dunder access, etc.) before exec() runs. Also checks for missing result
variables and large dimensions.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import List


@dataclass
class ValidationWarning:
    message: str
    severity: str = "warning"  # "warning" or "error"


# ── AST Whitelist Configuration ──────────────────────────────────────────────

# Modules that scripts are allowed to import
ALLOWED_IMPORTS = {"math", "cadquery", "cq"}

# Built-in functions that scripts may call
ALLOWED_BUILTINS = {
    "range", "len", "int", "float", "str", "bool", "list", "tuple",
    "dict", "set", "abs", "min", "max", "round", "sum",
    "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "True", "False", "None", "print", "isinstance", "type",
    "complex", "frozenset", "any", "all", "iter", "next",
    "hasattr", "repr",
}

# Functions that must never be called
BLOCKED_CALLS = {
    "exec", "eval", "compile", "open", "input", "__import__",
    "getattr", "setattr", "delattr", "globals", "locals",
    "breakpoint", "exit", "quit", "memoryview", "bytearray",
    "classmethod", "staticmethod", "property", "super",
    "vars", "dir",
}

# Attributes (dunder or otherwise) that must never be accessed
BLOCKED_ATTRIBUTES = {
    "__builtins__", "__class__", "__subclasses__", "__import__",
    "__globals__", "__code__", "__func__", "__self__",
    "__bases__", "__mro__", "__dict__", "__module__",
    "__qualname__", "__wrapped__", "__closure__",
    "__reduce__", "__reduce_ex__", "__getstate__",
}


class _ScriptSafetyChecker(ast.NodeVisitor):
    """AST visitor that flags dangerous constructs."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            module = alias.name.split(".")[0]
            if module not in ALLOWED_IMPORTS:
                self.errors.append(f"Forbidden import: '{alias.name}' (only {sorted(ALLOWED_IMPORTS)} allowed)")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            module = node.module.split(".")[0]
            if module not in ALLOWED_IMPORTS:
                self.errors.append(f"Forbidden import: 'from {node.module}' (only {sorted(ALLOWED_IMPORTS)} allowed)")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        # Check direct function calls like exec(), eval()
        if isinstance(node.func, ast.Name):
            if node.func.id in BLOCKED_CALLS:
                self.errors.append(f"Forbidden function call: '{node.func.id}()'")
        # Check method calls like obj.__class__()
        elif isinstance(node.func, ast.Attribute):
            if node.func.attr in BLOCKED_CALLS:
                self.errors.append(f"Forbidden function call: '.{node.func.attr}()'")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        # Block access to dangerous dunder attributes
        if node.attr in BLOCKED_ATTRIBUTES:
            self.errors.append(f"Forbidden attribute access: '.{node.attr}'")
        # Block any __dunder__ access except common safe ones
        elif node.attr.startswith("__") and node.attr.endswith("__"):
            safe_dunders = {"__init__", "__name__", "__len__", "__iter__",
                            "__next__", "__enter__", "__exit__",
                            "__add__", "__sub__", "__mul__", "__truediv__",
                            "__neg__", "__pos__", "__abs__",
                            "__eq__", "__ne__", "__lt__", "__gt__",
                            "__le__", "__ge__", "__hash__",
                            "__str__", "__repr__", "__bool__",
                            "__getitem__", "__setitem__", "__contains__"}
            if node.attr not in safe_dunders:
                self.errors.append(f"Forbidden dunder access: '.{node.attr}'")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if node.id == "__builtins__":
            self.errors.append("Direct access to '__builtins__' is forbidden")
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global):
        self.errors.append(f"'global' statement is forbidden (names: {node.names})")
        self.generic_visit(node)


def validate_script(script: str) -> List[ValidationWarning]:
    """Check script for safety issues, missing result var, and large dimensions.

    Uses AST analysis for security checks (imports, dangerous calls, dunder access)
    plus regex for dimension warnings.
    """
    warnings: List[ValidationWarning] = []

    # 1. Check for result/parts variable (quick string check)
    if "result" not in script and "parts" not in script:
        warnings.append(ValidationWarning("Script must assign to 'result' or 'parts'", "error"))

    # 2. Try to parse AST — syntax errors caught here
    try:
        tree = ast.parse(script)
    except SyntaxError as e:
        warnings.append(ValidationWarning(f"Syntax error: {e}", "error"))
        return warnings

    # 3. Run AST safety checker
    checker = _ScriptSafetyChecker()
    checker.visit(tree)

    for err in checker.errors:
        warnings.append(ValidationWarning(err, "error"))
    for warn in checker.warnings:
        warnings.append(ValidationWarning(warn, "warning"))

    # 4. Dimension sanity check (regex on source, same as before)
    numbers = re.findall(r"(?<![a-zA-Z_])(\d+\.?\d*)", script)
    for n in numbers:
        try:
            val = float(n)
        except ValueError:
            continue
        if val > 10000 and val != float("inf"):
            warnings.append(ValidationWarning(
                f"Large dimension detected: {val}mm (> 10 meters). Intentional?"
            ))

    # 5. Sweep reliability warning
    if ".sweep(" in script:
        warnings.append(ValidationWarning(
            "sweep() can be unreliable. Consider extrude + translate instead."
        ))

    return warnings


def has_blocking_errors(warnings: List[ValidationWarning]) -> bool:
    """Return True if any warning is severity='error'."""
    return any(w.severity == "error" for w in warnings)

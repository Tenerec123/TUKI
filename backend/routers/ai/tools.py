"""Auto-discovery system for tool functions.
Reads functions from read_tools and exec_tools modules,
generates JSON schemas from function signatures + docstrings."""

import inspect
import json
import re
import typing
from typing import Callable, Union, get_origin, get_args

from . import read_tools
from . import exec_tools

# ── Type mapping for JSON schema generation ────────────────────────────

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _py_type_to_json(tp):
    """Convert Python type hint to JSON schema type dict.
    Handles Optional[X], List[X], and simple types."""
    if tp is inspect.Parameter.empty:
        return {"type": "string"}

    origin = get_origin(tp)

    # Handle Optional[X] → Union[X, None]
    if origin is Union:
        args = get_args(tp)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            base = _TYPE_MAP.get(non_none[0], "string")
            return {"type": [base, "null"]}
        return {"type": "string"}

    # Handle List[X]
    if origin is list:
        args = get_args(tp)
        items = _py_type_to_json(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": items}

    base = _TYPE_MAP.get(tp, "string")
    return {"type": base}


def _parse_docstring(doc: str):
    """Parse standardized docstring into description, tool_type, and arg_descriptions.

    Format:
        First lines: description (everything before 'Type:').
        Type: read|write
        Args:
            param_name: Description text.

    Returns:
        (description, tool_type, arg_descriptions_dict)
    """
    desc_lines = []
    tool_type = None
    arg_descriptions = {}
    current_section = "desc"

    for line in doc.split('\n'):
        stripped = line.strip()

        if stripped.startswith('Type:'):
            tool_type = stripped.split(':', 1)[1].strip().lower()
            current_section = "type"
            continue

        if stripped.startswith('Args:'):
            current_section = "args"
            continue

        if current_section == "desc":
            if stripped:
                desc_lines.append(stripped)
        elif current_section == "args":
            m = re.match(r'^\s*(\w+):\s*(.+)$', stripped)
            if m:
                arg_descriptions[m.group(1)] = m.group(2).strip()

    description = ' '.join(desc_lines) if desc_lines else ""
    return description, tool_type, arg_descriptions


def _discover_tools():
    """Scan read_tools and exec_tools modules for tool functions.

    Each function must have a docstring with 'Type: read' or 'Type: write'.
    Function signature + docstring generate the JSON schema automatically.

    Returns:
        (ToolDict, tool_schemas, TOOL_READ_NAMES, TOOL_WRITE_NAMES)
    """
    ToolDict = {}
    tool_schemas = []
    TOOL_READ_NAMES = set()
    TOOL_WRITE_NAMES = set()

    modules = [
        (read_tools, 'read'),
        (exec_tools, 'write'),
    ]

    for module, default_type in modules:
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith('_'):
                continue

            doc = inspect.getdoc(func)
            if not doc:
                continue

            description, doc_type, arg_descriptions = _parse_docstring(doc)
            tool_type = doc_type or default_type

            if tool_type not in ('read', 'write'):
                continue

            sig = inspect.signature(func)

            properties = {}
            required = []

            for param_name, param in sig.parameters.items():
                json_type = _py_type_to_json(param.annotation)
                has_default = param.default is not inspect.Parameter.empty

                prop = dict(json_type)  # copy

                if param_name in arg_descriptions:
                    prop["description"] = arg_descriptions[param_name]

                properties[param_name] = prop

                if not has_default:
                    required.append(param_name)

            schema = {
                'type': 'function',
                'function': {
                    'name': name,
                    'description': description,
                    'parameters': {
                        'type': 'object',
                        'properties': properties,
                    }
                }
            }
            if required:
                schema['function']['parameters']['required'] = required

            ToolDict[name] = func
            tool_schemas.append(schema)

            if tool_type == 'read':
                TOOL_READ_NAMES.add(name)
            else:
                TOOL_WRITE_NAMES.add(name)

    return ToolDict, tool_schemas, TOOL_READ_NAMES, TOOL_WRITE_NAMES


# ── Run discovery once at import time ──────────────────────────────────

ToolDict, tool_schemas, TOOL_READ_NAMES, TOOL_WRITE_NAMES = _discover_tools()

# Derived lists
TOOL_SKIP_NAMES = set()
READ_TOOLS_SCHEMAS = [s for s in tool_schemas if s['function']['name'] in TOOL_READ_NAMES]
WRITE_TOOLS_SCHEMAS = [s for s in tool_schemas if s['function']['name'] in TOOL_WRITE_NAMES]
ALL_TOOLS_SCHEMAS = [s for s in tool_schemas if s['function']['name'] not in TOOL_SKIP_NAMES]


# ── Dispatch ───────────────────────────────────────────────────────────


def _sanitize_args(args: dict) -> dict:
    """Clean model-generated args before passing to tool functions.

    Models often send the string "null" instead of JSON null for optional fields.
    """
    cleaned = {}
    for key, value in args.items():
        if isinstance(value, str) and value.lower() in ("null", "none"):
            continue
        if value is None:
            continue
        cleaned[key] = value
    return cleaned


def execute_tool_call(name: str, arguments: str) -> str:
    """Execute a tool by name with JSON arguments string. Returns result JSON string."""
    func = ToolDict.get(name)
    if not func:
        print(f"[TOOL] {name} NOT FOUND in ToolDict")
        return f'"Error: Tool {name} not found"'
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        args = _sanitize_args(args)
        print(f"[TOOL] {name}(args={args})")
        result = func(**args)
        result_str = result if isinstance(result, str) else json.dumps(result, default=str)
        print(f"[TOOL] {name} → OK ({len(result_str)} chars)")
        return result_str
    except Exception as e:
        print(f"[TOOL] {name} → ERROR: {e}")
        return json.dumps(f"Execution Error: {str(e)}", default=str)

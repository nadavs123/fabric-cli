# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Find command for searching the OneLake catalog."""

import json
import os
import re
from argparse import Namespace
from typing import Any

import yaml

from fabric_cli.client import fab_api_catalog as catalog_api
from fabric_cli.core import fab_constant, fab_logger
from fabric_cli.core.fab_decorators import handle_exceptions, set_command_context
from fabric_cli.core.fab_exceptions import FabricCLIError
from fabric_cli.errors import ErrorMessages
from fabric_cli.utils import fab_jmespath as utils_jmespath
from fabric_cli.utils import fab_ui as utils_ui
from fabric_cli.utils import fab_util as utils


def _load_type_config() -> dict[str, list[str]]:
    """Load item type definitions from type_supported.yaml."""
    yaml_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "type_supported.yaml"
    )
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_TYPE_CONFIG = _load_type_config()
ALL_ITEM_TYPES = _TYPE_CONFIG["supported"] + _TYPE_CONFIG["unsupported"]
UNSUPPORTED_ITEM_TYPES = _TYPE_CONFIG["unsupported"]

_GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@handle_exceptions()
@set_command_context()
def find_command(args: Namespace) -> None:
    """Search the OneLake catalog for items, tables, workspaces, and workspace apps."""
    if args.query:
        args.query = utils.process_nargs(args.query)

    is_interactive = (
        getattr(args, "fab_mode", None) == fab_constant.FAB_MODE_INTERACTIVE
    )
    payload = _build_search_payload(args, is_interactive)

    utils_ui.print_grey("Searching...")

    if is_interactive:
        _find_interactive(args, payload)
    else:
        _find_commandline(args, payload)


def _fetch_results(
    args: Namespace, payload: dict[str, Any]
) -> tuple[list[dict], str | None]:
    """Execute a catalog search request and return parsed results.

    Returns:
        Tuple of (items list, continuation_token or None).

    Raises:
        FabricCLIError: On API error or invalid response body.
    """
    response = catalog_api.search(args, payload)
    results = _parse_response(response)

    items = results.get("value", [])
    continuation_token = results.get("continuationToken", "") or None
    return items, continuation_token


def _next_page_payload(token: str, current: dict[str, Any]) -> dict[str, Any]:
    """Build a continuation payload for the next page."""
    return {"continuationToken": token, "pageSize": current.get("pageSize", 50)}


def _print_search_summary(count: int, has_more_pages: bool = False) -> None:
    """Print the search result summary line."""
    label = "result" if count == 1 else "results"
    count_msg = f"{count} {label} found" + (" (more available)" if has_more_pages else "")
    utils_ui.print_grey("")
    utils_ui.print_grey(count_msg)
    utils_ui.print_grey("")


def _display_page(
    args: Namespace,
    display_items: list[dict],
    truncate_cols: list[str] | None,
    has_more_pages: bool,
    total_count: int,
) -> int:
    """Display a page of results, returning the updated total count."""
    if display_items:
        total_count += len(display_items)
        _print_search_summary(total_count, has_more_pages)
        _display_items(args, display_items, truncate_cols)
    return total_count


def _find_interactive(args: Namespace, payload: dict[str, Any]) -> None:
    """Fetch and display results page by page, prompting between pages."""
    total_count = 0
    items, continuation_token = _fetch_results(args, payload)
    display_items, truncate_cols = _prepare_display_items(args, items)
    total_count = _display_page(
        args, display_items, truncate_cols, continuation_token is not None, total_count
    )

    while continuation_token is not None:
        if display_items:
            try:
                utils_ui.print_grey("")
                input("Press Enter to continue... (Ctrl+C to stop)")
            except (KeyboardInterrupt, EOFError):
                utils_ui.print_grey("")
                break

        payload = _next_page_payload(continuation_token, payload)
        items, continuation_token = _fetch_results(args, payload)
        display_items, truncate_cols = _prepare_display_items(args, items)
        total_count = _display_page(
            args, display_items, truncate_cols, continuation_token is not None, total_count
        )

    if total_count == 0:
        utils_ui.print_grey("No results found.")


def _find_commandline(args: Namespace, payload: dict[str, Any]) -> None:
    """Fetch all results across pages and display."""
    all_items: list[dict] = []
    items, continuation_token = _fetch_results(args, payload)
    all_items.extend(items)

    while continuation_token is not None:
        payload = _next_page_payload(continuation_token, payload)
        items, continuation_token = _fetch_results(args, payload)
        all_items.extend(items)

    if not all_items:
        utils_ui.print_grey("No results found.")
        return

    display_items, truncate_cols = _prepare_display_items(args, all_items)
    if not display_items:
        utils_ui.print_grey("No results found.")
        return
    _print_search_summary(len(display_items))
    _display_items(args, display_items, truncate_cols)


def _build_search_payload(args: Namespace, is_interactive: bool) -> dict[str, Any]:
    """Build the search request payload from command arguments."""
    request: dict[str, Any] = {"search": args.search_text}
    request["pageSize"] = 30 if is_interactive else 1000

    filters = _parse_filter_params(args)
    clauses = []
    if "type" in filters:
        clauses.append(_build_type_clause(filters["type"]))
    if "workspace" in filters:
        clauses.append(_build_workspace_clause(filters["workspace"]))
    if clauses:
        request["filter"] = " and ".join(clauses)

    return request


def _quote_filter_value(value: str) -> str:
    """Quote a filter value as an OData string literal."""
    return "'" + value.replace("'", "''") + "'"


def _combine_clauses(clauses: list[str], operator: str, multiple: bool) -> str:
    """Join per-value clauses: 'or' for eq, 'and' for ne, grouped when needed."""
    joiner = " or " if operator == "eq" else " and "
    combined = joiner.join(clauses)
    return f"({combined})" if multiple else combined


def _build_type_clause(type_filter: dict[str, Any]) -> str:
    """Build the Type clause of the filter."""
    op = type_filter["operator"]
    values = type_filter["values"]
    clauses = [f"Type {op} {_quote_filter_value(v)}" for v in values]
    return _combine_clauses(clauses, op, len(clauses) > 1)


def _build_workspace_clause(workspace_filter: dict[str, Any]) -> str:
    """Build the workspace clause, by ID for GUIDs and by display name otherwise."""
    op = workspace_filter["operator"]
    clauses = []
    for value in workspace_filter["values"]:
        prop = (
            "Hierarchy/Workspace/Id"
            if _GUID_PATTERN.match(value)
            else "Hierarchy/Workspace/DisplayName"
        )
        clauses.append(f"{prop} {op} {_quote_filter_value(value)}")
    return _combine_clauses(clauses, op, len(clauses) > 1)


def _parse_filter_params(args: Namespace) -> dict[str, dict[str, Any]]:
    """Extract and validate filters from -P params.

    Supports the 'type' and 'workspace' keys, each with = (eq) or != (ne) and
    a single value or a bracketed list:
        -P type=Report                     -> eq single
        -P type=[Report,Lakehouse]         -> eq multiple (or)
        -P type!=[Dashboard,Report]        -> ne multiple (and)
        -P workspace=Sales                 -> workspace display name
        -P workspace=<workspace-id>        -> workspace ID
        -P type=Table,workspace=[Sales,Ops]

    Returns a dict keyed by 'type' and/or 'workspace', each with 'operator'
    ('eq' or 'ne') and a 'values' list.
    """
    params_str = getattr(args, "params", None)
    if not params_str:
        return {}

    # nargs="?" gives a string; nargs="*" gives a list (backward compat)
    if isinstance(params_str, list):
        params_str = ",".join(params_str)

    params_dict = utils.get_dict_from_params(params_str, max_depth=1)

    filters: dict[str, dict[str, Any]] = {}
    for key, value in params_dict.items():
        # get_dict_from_params splits on first "=", so "type!=X" becomes key="type!", value="X"
        clean_key = key.rstrip("!").lower()
        operator = "ne" if key.endswith("!") else "eq"
        if clean_key not in ("type", "workspace"):
            raise FabricCLIError(
                ErrorMessages.Common.unsupported_parameter(key.rstrip("!")),
                fab_constant.ERROR_INVALID_INPUT,
            )
        values = _split_param_values(value)
        if not values:
            continue
        if clean_key == "type":
            values = _normalize_types(values, operator)
        filters[clean_key] = {"operator": operator, "values": values}

    return filters


def _split_param_values(value: str) -> list[str]:
    """Split a bracketed list ([a,b]) or a single value into a list of values."""
    value = (value or "").strip()
    if value.startswith("[") and value.endswith("]"):
        values = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        if not values:
            raise FabricCLIError(
                ErrorMessages.Find.unrecognized_type(
                    "[]", " Specify at least one value."
                ),
                fab_constant.ERROR_INVALID_INPUT,
            )
        return values
    return [value] if value else []


def _normalize_types(types: list[str], operator: str) -> list[str]:
    """Normalize known type names to their canonical casing.

    Unknown types are passed through unchanged so new catalog entry types work
    without a CLI update; the API rejects or returns nothing for invalid ones.
    """
    all_types_lower = {t.lower(): t for t in ALL_ITEM_TYPES}
    unsupported_lower = {t.lower() for t in UNSUPPORTED_ITEM_TYPES}
    normalized = []
    for t in types:
        t_lower = t.lower()
        if t_lower in unsupported_lower and operator == "eq":
            raise FabricCLIError(
                ErrorMessages.Common.type_not_supported(all_types_lower[t_lower]),
                fab_constant.ERROR_UNSUPPORTED_ITEM_TYPE,
            )
        normalized.append(all_types_lower.get(t_lower, t))
    return normalized


def _parse_response(response) -> dict:
    """Parse a successful API response or raise FabricCLIError on failure."""
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        if response.status_code == 200:
            raise FabricCLIError(
                ErrorMessages.Common.invalid_json_format(),
                fab_constant.ERROR_INVALID_JSON,
            )
        raise FabricCLIError(
            ErrorMessages.Find.search_failed(response.text),
            fab_constant.ERROR_INVALID_JSON,
        )

    if response.status_code == 200:
        return data

    fab_logger.log_debug(f"Catalog search error: {data}")
    raise FabricCLIError(
        ErrorMessages.Find.search_failed(data.get("message", response.text)),
        data.get("errorCode", fab_constant.ERROR_UNEXPECTED_ERROR),
    )


def _get_workspace_field(item: dict, field: str) -> str | None:
    """Extract workspace field, supporting both flat and hierarchy formats."""
    ws = (item.get("hierarchy") or {}).get("workspace") or {}
    if field == "name":
        return ws.get("displayName") or item.get("workspaceName")
    if field == "id":
        return ws.get("id") or item.get("workspaceId")
    return None


def _get_parent_item(item: dict) -> dict:
    """Return the owning item of an object entry such as a table, or {}."""
    return (item.get("hierarchy") or {}).get("item") or {}


def _get_parent_object_name(item: dict) -> str | None:
    """Return the parent object name of an object entry, such as its schema."""
    return ((item.get("hierarchy") or {}).get("object") or {}).get("displayName")


def _prepare_display_items(
    args: Namespace, items: list[dict]
) -> tuple[list[dict], list[str] | None]:
    """Transform API items into display-ready dicts with optional filtering.

    Returns:
        Tuple of (display items, columns_to_truncate or None).
    """
    show_details = getattr(args, "long", False)
    has_descriptions = any(item.get("description") for item in items)
    has_parent_items = any(_get_parent_item(item) for item in items)

    display_items = []
    for item in items:
        parent_item = _get_parent_item(item)
        if show_details:
            entry = {
                "name": item.get("displayName") or item.get("name"),
                "id": item.get("id"),
                "type": item.get("type"),
                "workspace": _get_workspace_field(item, "name"),
                "workspace_id": _get_workspace_field(item, "id"),
            }
            if has_parent_items:
                entry["item_type"] = parent_item.get("type")
                entry["item_id"] = parent_item.get("id")
                entry["schema"] = _get_parent_object_name(item)
        else:
            entry = {
                "name": item.get("displayName") or item.get("name"),
                "type": item.get("type"),
                "workspace": _get_workspace_field(item, "name"),
            }
            if has_parent_items:
                entry["item_type"] = parent_item.get("type")
        if has_descriptions:
            entry["description"] = item.get("description") or ""
        display_items.append(entry)

    if getattr(args, "query", None):
        query_result = utils_jmespath.search(display_items, args.query)
        if not isinstance(query_result, list):
            return [], None
        display_items = query_result

    truncate_cols = ["description", "workspace", "name"] if not show_details else None
    return display_items, truncate_cols


def _display_items(
    args: Namespace,
    display_items: list[dict],
    columns_to_truncate: list[str] | None = None,
) -> None:
    """Render prepared display items, truncating columns for text format."""
    utils_ui.print_output_format(
        args,
        data=display_items,
        show_headers=True,
        truncate_columns=columns_to_truncate,
    )
    utils_ui.print_grey("")

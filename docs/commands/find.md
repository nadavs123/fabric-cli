# `find` Command

Search the OneLake catalog for items, tables, workspaces, and workspace apps across all workspaces you can access.

**Usage:**

```
fab find <query> [-P <params>] [-l] [-q <query>]
```

**Parameters:**

- `<query>`: Search text. Matches display name, description, and workspace name. For tables, it also matches column names and descriptions and returns the table that contains the column. Wrap text in double quotes for an exact phrase.
- `-P, --params`: Filters in `key=value` or `key!=value` format. Use brackets for multiple values: `type=[Lakehouse,Notebook]`. Use `!=` to exclude: `type!=Dashboard`. Combine keys with commas: `type=Table,workspace=Finance`.
- `-l, --long`: Show detailed table with IDs (name, id, type, workspace, workspace_id, description, and for tables item_type, item_id, schema). Optional.
- `-q, --query`: JMESPath query to filter results client-side. Optional.

**Supported filter parameters:**

| Parameter   | Description |
|-------------|-------------|
| `type`      | Filter by entry type: an item type such as `Lakehouse` or `Report`, or `Table`, `Workspace`, or `WorkspaceApp`. Type names are case-insensitive and are passed to the Catalog Search API, which returns no results for types it doesn't support. `Dashboard` isn't searchable. |
| `workspace` | Filter by workspace display name or workspace ID. Up to 12 values. |

Both parameters support `=` (default) and `!=`.

**Examples:**

```
# search for items by name or description
fab find 'sales report'

# search for lakehouses only
fab find 'data' -P type=Lakehouse

# find tables by table or column name
fab find 'customer_id' -P type=Table

# search within a workspace (by name or ID)
fab find 'sales' -P workspace=Finance

# find a workspace by name
fab find '"Finance"' -P type=Workspace

# search for multiple item types (bracket syntax)
fab find 'dashboard' -P type=[Report,SemanticModel]

# exclude a type
fab find 'data' -P type!=Dashboard

# show detailed output with IDs
fab find 'sales' -l

# combine filters
fab find 'finance' -P type=[Warehouse,Lakehouse],workspace=Finance -l

# project specific fields
fab find 'data' -q "[].{name: name, workspace: workspace}"
```

**Behavior:**

- Results include only entries you have access to.
- Table results show the owning item's type (`item_type`). With `-l`, they also show the owning item's ID (`item_id`) and the schema that contains the table.
- Tables are indexed for some item types only. If a table isn't found, query the owning item's own metadata instead.
- New items can take a few minutes to appear in search results.
- In interactive mode (`fab` shell), results are paged 30 at a time with "Press Enter to continue..." prompts.
- In command-line mode (`fab find ...`), all results are fetched by paginating through the full result set.
- The `-q` JMESPath filter is applied client-side after results are returned from the API.
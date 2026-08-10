import { useMemo, useState, type ReactNode } from "react";
import { Empty } from "./Feedback";

export interface Column<T> {
  key: string;
  label: string;
  value: (row: T) => unknown;
  render?: (row: T) => ReactNode;
  numeric?: boolean;
  sortable?: boolean;
}
export function DataTable<T>({
  rows,
  columns,
  rowKey,
  label,
  pageSize = 25,
  actions,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  label: string;
  pageSize?: number;
  actions?: (row: T) => ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState(
    columns.find((column) => column.sortable)?.key ?? columns[0].key,
  );
  const [descending, setDescending] = useState(false);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const processed = useMemo(() => {
    const found = rows.filter((row) =>
      columns.some((column) =>
        String(column.value(row) ?? "")
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    );
    const column = columns.find((item) => item.key === sort);
    if (column)
      found.sort((a, b) =>
        String(column.value(a) ?? "").localeCompare(
          String(column.value(b) ?? ""),
          undefined,
          { numeric: true },
        ),
      );
    return descending ? found.reverse() : found;
  }, [rows, columns, query, sort, descending]);
  const pages = Math.max(1, Math.ceil(processed.length / pageSize));
  const visible = processed.slice(
    Math.min(page, pages - 1) * pageSize,
    Math.min(page + 1, pages) * pageSize,
  );
  if (!rows.length) return <Empty />;
  const toggle = (key: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  return (
    <div className="table-wrap">
      <div className="table-tools">
        <label>
          <span className="sr-only">Search {label}</span>
          <input
            className="input search"
            type="search"
            placeholder={`Search ${label.toLowerCase()}…`}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
          />
        </label>
        <span>
          {selected.size
            ? `${selected.size} selected`
            : `${processed.length} records`}
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <caption className="sr-only">{label}</caption>
          <thead>
            <tr>
              <th className="select-cell">
                <span className="sr-only">Select</span>
              </th>
              {columns.map((column) => (
                <th
                  key={column.key}
                  className={column.numeric ? "numeric" : ""}
                >
                  {column.sortable === false ? (
                    column.label
                  ) : (
                    <button
                      className="sort"
                      onClick={() => {
                        if (sort === column.key) setDescending(!descending);
                        else {
                          setSort(column.key);
                          setDescending(false);
                        }
                      }}
                    >
                      {column.label}
                      {sort === column.key ? (descending ? " ↓" : " ↑") : ""}
                    </button>
                  )}
                </th>
              ))}
              {actions && <th>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => {
              const key = rowKey(row);
              return (
                <tr key={key}>
                  <td className="select-cell">
                    <input
                      type="checkbox"
                      aria-label={`Select row ${key}`}
                      checked={selected.has(key)}
                      onChange={() => toggle(key)}
                    />
                  </td>
                  {columns.map((column) => (
                    <td
                      key={column.key}
                      className={column.numeric ? "numeric" : ""}
                    >
                      {column.render
                        ? column.render(row)
                        : format(column.value(row))}
                    </td>
                  ))}
                  {actions && <td>{actions(row)}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <nav className="pagination" aria-label={`${label} pages`}>
          <button
            className="button"
            disabled={page <= 0}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </button>
          <span>
            Page {Math.min(page + 1, pages)} of {pages}
          </span>
          <button
            className="button"
            disabled={page >= pages - 1}
            onClick={() => setPage(page + 1)}
          >
            Next
          </button>
        </nav>
      )}
    </div>
  );
}

function format(value: unknown): ReactNode {
  if (value === null || value === undefined || value === "")
    return <span className="muted">—</span>;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

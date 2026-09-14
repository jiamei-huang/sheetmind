import React, { useMemo } from "react";
import { X } from "lucide-react";

/** pandas generates "Unnamed: N" for empty header cells */
const isUnnamed = (col) => /^Unnamed:\s*\d+/i.test(String(col));

export default function SheetPreviewModal({ sheetName, columns = [], rows = [], onClose }) {
  const numericColumns = useMemo(() => new Set(columns.filter((column) => {
    const values = rows
      .map((row) => row[column])
      .filter((value) => value !== null && value !== undefined && value !== "");
    return values.length > 0 && values.every((value) => (
      typeof value === "number" || Number.isFinite(Number(String(value).replace(/,/g, "")))
    ));
  })), [columns, rows]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-4" role="dialog" aria-modal="true" aria-labelledby="sheet-preview-title">
      {/* Header stays visible while the table body scrolls independently. */}
      <div className="sm-dialog flex max-h-[85vh] w-[92vw] max-w-5xl flex-col">

        <div className="flex flex-shrink-0 items-start justify-between border-b border-slate-200 px-4 py-3 sm:px-6 sm:py-4">
          <div className="min-w-0 pr-4">
          <h3 id="sheet-preview-title" className="truncate text-base font-semibold text-slate-900" title={sheetName}>
            Preview: {sheetName}
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">Showing the first {rows.length} rows</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded-full transition-colors"
            aria-label="Close preview"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-4 pb-4 sm:px-6">
          <table className="table-auto whitespace-nowrap divide-y divide-slate-200 text-sm w-full">
            <thead>
              <tr>
                {columns.map((col, i) => (
                  <th
                    key={`${col}-${i}`}
                    className={[
                      // sticky must be on <th> itself — thead has no stacking context
                      `sticky top-0 z-10 bg-slate-100 px-4 py-2.5 text-xs font-semibold ${numericColumns.has(col) ? "text-right" : "text-left"}`,
                      isUnnamed(col) ? "text-slate-400 italic" : "text-slate-600",
                    ].join(" ")}
                    title={String(col)}
                  >
                    {/* Show blank for Unnamed placeholders so the table isn't noisy */}
                    {isUnnamed(col) ? "—" : String(col)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-slate-100">
              {rows.length > 0 ? (
                rows.map((row, rowIndex) => (
                  <tr key={rowIndex} className="hover:bg-slate-50 transition-colors">
                    {columns.map((col, i) => (
                      <td key={`${col}-${i}`} className={`px-4 py-2.5 text-slate-700 ${numericColumns.has(col) ? "text-right tabular-nums" : "text-left"}`}>
                        {row[col] !== null && row[col] !== undefined ? String(row[col]) : ""}
                      </td>
                    ))}
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-8 text-center text-slate-500">
                    No data
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

      </div>
    </div>
  );
}

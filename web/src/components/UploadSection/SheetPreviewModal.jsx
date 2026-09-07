import React from "react";
import { X } from "lucide-react";

/** pandas generates "Unnamed: N" for empty header cells */
const isUnnamed = (col) => /^Unnamed:\s*\d+/i.test(String(col));

export default function SheetPreviewModal({ sheetName, columns = [], rows = [], onClose }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 px-4">
      {/*
        Modal: flex column so header + footer are always visible and the
        table area scrolls independently.  max-h-[85vh] caps overall height.
      */}
      <div className="bg-white rounded-xl shadow-2xl flex flex-col w-[90vw] max-w-5xl max-h-[85vh]">

        {/* ── Header ─────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4 flex-shrink-0">
          <h3 className="text-lg font-semibold text-slate-900 truncate pr-4" title={sheetName}>
            Preview: {sheetName}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="p-2 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded-full transition-colors"
            aria-label="Close preview"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ── Scrollable table area ───────────────────────────────────── */}
        {/* NO top padding here — any pt would scroll away and let data   */}
        {/* rows sneak above the sticky <th> (sticky top-0 anchors to the */}
        {/* scroll container's top edge, not its content edge).           */}
        <div className="flex-1 min-h-0 overflow-auto px-6 pb-4">
          <table className="table-auto whitespace-nowrap divide-y divide-slate-200 text-sm w-full">
            <thead>
              <tr>
                {columns.map((col, i) => (
                  <th
                    key={`${col}-${i}`}
                    className={[
                      // sticky must be on <th> itself — thead has no stacking context
                      "sticky top-0 z-10 bg-slate-100 px-4 py-2 text-left text-xs font-semibold tracking-wide",
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
                      <td key={`${col}-${i}`} className="px-4 py-2 text-slate-700">
                        {row[col] !== null && row[col] !== undefined ? String(row[col]) : ""}
                      </td>
                    ))}
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={columns.length} className="px-4 py-8 text-center text-slate-500">
                    暂无数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ── Footer — always pinned to bottom ───────────────────────── */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-200 flex-shrink-0">
          <span className="text-xs text-slate-400">
            显示前 {rows.length} 行
          </span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md transition-colors"
          >
            Close
          </button>
        </div>

      </div>
    </div>
  );
}

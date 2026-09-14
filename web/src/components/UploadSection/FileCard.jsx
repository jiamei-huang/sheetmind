import React, { useEffect, useMemo, useState } from "react";
import { FileSpreadsheet, Clock, HardDrive, ChevronDown, ChevronUp, Trash2 } from "lucide-react";
import SheetSelector from "./SheetSelector";

export default function FileCard({
  file,
  onDelete,
  onUpdateSelectedSheets,
  onTogglePanel,
  projectId,
  onShowToast,
  onShowErrorModal,
}) {
  const [isExpanded, setIsExpanded] = useState(file.isExpanded ?? false);
  const [draftSelectedSheets, setDraftSelectedSheets] = useState(file.selectedSheets ?? []);

  useEffect(() => {
    setIsExpanded(file.isExpanded ?? false);
  }, [file.isExpanded]);

  useEffect(() => {
    if (isExpanded) {
      setDraftSelectedSheets(file.selectedSheets ?? []);
    }
  }, [file.selectedSheets, isExpanded]);

  const selectedSheetsLabel = useMemo(() => {
    if (!file.selectedSheets?.length) {
      return "No sheets selected yet";
    }
    return `Selected Sheets: ${file.selectedSheets.join(", ")}`;
  }, [file.selectedSheets]);

  const handleToggleExpanded = () => {
    const next = !isExpanded;
    setIsExpanded(next);
    onTogglePanel(file.id, next);
  };

  const handleConfirmSelection = () => {
    onUpdateSelectedSheets(file.id, draftSelectedSheets);
    setIsExpanded(false);
  };

  return (
    <div className="space-y-3 py-3 first:pt-1 last:pb-1">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex-1 space-y-2">
          <div className="flex items-center gap-3">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-emerald-50 text-emerald-600">
              <FileSpreadsheet className="w-5 h-5" />
            </span>
            <div>
              <p className="truncate text-sm font-semibold text-slate-900" title={file.name}>
                {file.name}
              </p>
              <p className="text-xs text-slate-500">{selectedSheetsLabel}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500">
            <div className="flex items-center gap-1.5">
              <HardDrive className="w-4 h-4" />
              <span>{file.size}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Clock className="w-4 h-4" />
              <span>{file.uploadedAt}</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleToggleExpanded}
            aria-label={`${isExpanded ? "Hide sheets for" : "Select sheets for"} ${file.name}`}
            className="sm-control-secondary inline-flex items-center gap-1"
          >
            {isExpanded ? (
              <>
                <ChevronUp className="w-4 h-4" />
                Hide Sheets
              </>
            ) : (
              <>
                <ChevronDown className="w-4 h-4" />
                Select Sheets
              </>
            )}
          </button>

          <button
            type="button"
            onClick={() => onDelete(file.id)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-red-50 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            aria-label={`Delete ${file.name}`}
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {isExpanded && (
        <SheetSelector
          sheets={file.sheets}
          draftSelectedSheets={draftSelectedSheets}
          projectId={file.projectId || projectId} // 优先使用文件对象中的 projectId
          fileId={file.fileId || file.id}
          fileName={file.name}
          onToggleSheet={(sheetName) => {
            setDraftSelectedSheets((prev) =>
              prev.includes(sheetName)
                ? prev.filter((name) => name !== sheetName)
                : [...prev, sheetName]
            );
          }}
          onConfirm={handleConfirmSelection}
          onCancel={() => {
            setIsExpanded(false);
            onTogglePanel(file.id, false);
          }}
          onShowToast={onShowToast}
          onShowErrorModal={onShowErrorModal}
        />
      )}
    </div>
  );
}

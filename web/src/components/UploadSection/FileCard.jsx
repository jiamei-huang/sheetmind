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
    <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-5 space-y-4">
      <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div className="flex-1 space-y-2">
          <div className="flex items-center gap-3">
            <span className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-green-50 text-green-600">
              <FileSpreadsheet className="w-5 h-5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-gray-900">{file.name}</p>
              <p className="text-xs text-gray-500">{selectedSheetsLabel}</p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4 text-xs text-gray-500">
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
            className="inline-flex items-center gap-1 px-4 py-2 text-sm font-medium bg-blue-50 text-blue-700 hover:bg-blue-100 rounded-md border border-blue-200 transition-colors"
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
            className="inline-flex items-center justify-center w-10 h-10 rounded-md bg-[#F6F8FB] text-gray-500 hover:bg-red-50 transition-colors hover:text-red-500"
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

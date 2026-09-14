import React, { useMemo, useState } from "react";
import { Eye, Loader } from "lucide-react";
import SheetPreviewModal from "./SheetPreviewModal";
import { previewExcelFile } from "../../api/files";

export default function SheetSelector({
  sheets = [],
  draftSelectedSheets = [],
  onToggleSheet,
  onConfirm,
  onCancel,
  projectId,
  fileId,
  fileName,
  onShowToast,
  onShowErrorModal,
}) {
  const [previewConfig, setPreviewConfig] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingSheetName, setLoadingSheetName] = useState(null);

  const sheetList = useMemo(() => {
    if (!sheets?.length) {
      return ["Sheet1", "Sheet2", "Sheet3"];
    }
    return sheets;
  }, [sheets]);

  const handleOpenPreview = async (sheetName) => {
    // 如果没有 projectId 或 fileName，显示错误
    if (!projectId || !fileId || !fileName) {
      console.error("[SheetSelector] Missing projectId or fileName, cannot load preview");
      onShowErrorModal?.({
        title: "Preview failed",
        message: "Missing project ID or file name. The preview could not be loaded.",
      });
      return;
    }

    // 调用后端 API 获取真实预览数据
    setLoadingSheetName(sheetName);
    setLoadingPreview(true);

    try {
      const response = await previewExcelFile({
        projectId,
        fileId,
        fileName,
      });

      // 从响应中找到对应的 sheet 数据
      const sheetData = response.sheets?.find(
        (sheet) => sheet.sheetName === sheetName
      );


      if (sheetData) {
        const columns = sheetData.columns || [];
        const rows = sheetData.preview || [];

        setPreviewConfig({
          sheetName: sheetData.sheetName,
          columns: columns,
          rows: rows,
        });
      } else {
        // 如果找不到对应的 sheet，显示错误
        onShowErrorModal?.({
          title: "Preview failed",
          message: `No preview data was found for sheet "${sheetName}".`,
        });
      }
    } catch (error) {
      console.error("[SheetSelector] Error loading preview:", error);
      const errorMessage =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        "Could not load preview data. Please try again.";
      onShowErrorModal?.({
        title: "Preview failed",
        message: errorMessage,
      });
    } finally {
      setLoadingPreview(false);
      setLoadingSheetName(null);
    }
  };

  const handleClosePreview = () => {
    setPreviewConfig(null);
  };

  return (
    <div className="space-y-3 rounded-md bg-slate-50 p-3 sm:p-4">
      <div>
        <h4 className="text-sm font-semibold text-slate-800">Available Sheets</h4>
        <p className="text-xs text-slate-500 mt-1">Select one or more sheets to analyze</p>
      </div>
      <div className="grid grid-cols-1 gap-1 sm:grid-cols-2 sm:gap-x-4">
        {sheetList.map((sheetName) => {
          const isChecked = draftSelectedSheets.includes(sheetName);
          return (
            <div
              key={sheetName}
              className={`flex min-h-[52px] items-center justify-between gap-3 rounded-md px-3 py-2 transition-colors sm:min-w-[240px] ${
                isChecked
                  ? "bg-blue-50 text-blue-700"
                  : "hover:bg-white"
              }`}
            >
              <label className={`flex items-center gap-3 text-sm font-medium flex-1 min-w-0 ${
                isChecked ? "text-blue-600" : "text-slate-700"
              }`}>
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => onToggleSheet(sheetName)}
                  className="w-4 h-4 border-slate-300 rounded accent-blue-600 focus:ring-blue-500"
                />
                <span className="truncate block max-w-full" title={sheetName}>
                  {sheetName}
                </span>
              </label>

              <button
                type="button"
                onClick={() => handleOpenPreview(sheetName)}
                disabled={loadingPreview}
                className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {loadingPreview && loadingSheetName === sheetName ? (
                  <>
                    <Loader className="w-3.5 h-3.5 animate-spin" />
                    Loading...
                  </>
                ) : (
                  <>
                    <Eye className="w-3.5 h-3.5" />
                    View Preview
                  </>
                )}
              </button>
            </div>
          );
        })}
      </div>

      <div className="flex flex-col gap-2 pt-1 sm:flex-row sm:items-center sm:justify-end">
        <button
          type="button"
          onClick={() => {
            handleClosePreview();
            onCancel?.();
          }}
          className="px-4 py-2 text-sm font-medium text-slate-600 bg-white border border-slate-200 rounded-md hover:bg-slate-100 transition-colors w-full sm:w-auto"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => {
            handleClosePreview();
            onConfirm();
          }}
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-md transition-colors w-full sm:w-auto"
        >
          Confirm Selection
        </button>
      </div>

      {previewConfig && (
        <SheetPreviewModal
          sheetName={previewConfig.sheetName}
          columns={previewConfig.columns}
          rows={previewConfig.rows}
          onClose={handleClosePreview}
        />
      )}
    </div>
  );
}

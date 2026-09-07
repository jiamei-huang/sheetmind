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
    if (!projectId || !fileName) {
      console.error("[SheetSelector] Missing projectId or fileName, cannot load preview");
      onShowErrorModal?.({
        title: "预览失败",
        message: "缺少项目ID或文件名，无法加载预览数据",
      });
      return;
    }

    // 调用后端 API 获取真实预览数据
    setLoadingSheetName(sheetName);
    setLoadingPreview(true);

    try {
      const response = await previewExcelFile({
        projectId,
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
          title: "预览失败",
          message: `未找到 Sheet "${sheetName}" 的预览数据`,
        });
      }
    } catch (error) {
      console.error("[SheetSelector] Error loading preview:", error);
      const errorMessage =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        "加载预览数据失败，请稍后重试";
      onShowErrorModal?.({
        title: "预览失败",
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
    <div className="bg-[#f5f7fa] rounded-lg border border-gray-200 p-4 space-y-4">
      <div>
        <h4 className="text-sm font-semibold text-gray-800">Available Sheets</h4>
        <p className="text-xs text-gray-500 mt-1">Select one or more sheets to analyze</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {sheetList.map((sheetName) => {
          const isChecked = draftSelectedSheets.includes(sheetName);
          return (
            <div
              key={sheetName}
              className={`flex items-center justify-between gap-3 rounded-md px-3 py-2 border transition-colors min-h-[56px] sm:min-w-[240px] ${
                isChecked
                  ? "border-blue-300 bg-blue-50/80 ring-1 ring-blue-200"
                  : "border-gray-200 bg-white"
              }`}
            >
              <label className={`flex items-center gap-3 text-sm font-medium flex-1 min-w-0 ${
                isChecked ? "text-blue-600" : "text-gray-700"
              }`}>
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => onToggleSheet(sheetName)}
                  className="w-4 h-4 border-gray-300 rounded accent-blue-600 focus:ring-blue-500"
                />
                <span className="truncate block max-w-full" title={sheetName}>
                  {sheetName}
                </span>
              </label>

              <button
                type="button"
                onClick={() => handleOpenPreview(sheetName)}
                disabled={loadingPreview}
                className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium text-gray-700 bg-[#f5f7fa] hover:bg-[#e5e7eb] rounded-md transition-colors flex-shrink-0 whitespace-nowrap disabled:opacity-50 disabled:cursor-not-allowed"
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

      <div className="flex flex-col sm:flex-row sm:justify-end sm:items-center gap-3 pt-2">
        <button
          type="button"
          onClick={() => {
            handleClosePreview();
            onCancel?.();
          }}
          className="px-4 py-2 text-sm font-medium text-gray-600 bg-white border border-gray-200 rounded-md hover:bg-gray-100 transition-colors w-full sm:w-auto"
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

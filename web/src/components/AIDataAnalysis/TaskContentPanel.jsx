import React, { lazy, Suspense, useMemo } from "react";
import {
  Brain,
  AlertCircle,
  CheckCircle2,
  Circle,
  Database,
  Info,
  Lightbulb,
  Download,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ImageDown,
} from "lucide-react";
import Loader from "../Loader";
import { CHART_TYPES, VISUALIZATION_MODES, VISUALIZATION_LABELS } from "../../constants/chart";

const RechartsVisualization = lazy(() => import("./RechartsVisualization"));

const ChartView = (props) => (
  <Suspense fallback={<Loader label="Loading chart..." />}>
    <RechartsVisualization {...props} />
  </Suspense>
);

const maxChars = 500;
const QUICK_SUGGESTIONS = [
  "Merge all sheets and remove duplicates",
  "Which product line grows the fastest?",
  "Clean sales data and show monthly trends",
  "Combine financial data and create performance dashboard",
];

const PROGRESS_STAGES = [
  { key: "thinking", label: "理解问题" },
  { key: "select", label: "选择数据" },
  { key: "load", label: "加载 Excel" },
  { key: "execute", label: "执行分析" },
  { key: "chart", label: "生成图表" },
  { key: "insight", label: "整理洞察" },
];

const RESULT_TYPE_LABELS = {
  data: "数据表",
  chart: "图表",
  both: "数据表 + 图表",
  insight_only: "洞察",
  "insights-only": "洞察",
};

const inferProgressStage = (message = "") => {
  if (message.includes("选择")) return 1;
  if (message.includes("加载")) return 2;
  if (message.includes("筛选") || message.includes("执行") || message.includes("分析数据") || message.includes("代码")) return 3;
  if (message.includes("图表") || message.includes("图")) return 4;
  if (message.includes("洞察") || message.includes("总结")) return 5;
  return 0;
};

const formatNumber = (value) => {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value.toLocaleString() : "—";
  }
  return value;
};

const buildChartDataRows = (chartData) => {
  if (!chartData?.labels?.length || !chartData?.series?.length) {
    return [];
  }
  return chartData.labels.map((label, rowIndex) => ({
    label,
    values: chartData.series.map((series) => series.values?.[rowIndex] ?? null),
  }));
};


const buildPaginationRange = (current, total, siblingCount = 2) => {
  if (total <= 1) {
    return [1];
  }

  const totalNumbers = siblingCount * 2 + 5;

  if (total <= totalNumbers) {
    return Array.from({ length: total }, (_, index) => index + 1);
  }

  const range = [];
  const leftSiblingIndex = Math.max(current - siblingCount, 2);
  const rightSiblingIndex = Math.min(current + siblingCount, total - 1);

  range.push(1);

  if (leftSiblingIndex > 2) {
    range.push("left-ellipsis");
  } else {
    for (let page = 2; page < leftSiblingIndex; page += 1) {
      range.push(page);
    }
  }

  for (let page = leftSiblingIndex; page <= rightSiblingIndex; page += 1) {
    range.push(page);
  }

  if (rightSiblingIndex < total - 1) {
    range.push("right-ellipsis");
  } else {
    for (let page = rightSiblingIndex + 1; page < total; page += 1) {
      range.push(page);
    }
  }

  range.push(total);

  return range;
};

const buildPreviewTotals = (columns, rows) => {
  if (!Array.isArray(columns) || !Array.isArray(rows)) {
    return {};
  }

  const parseNumericValue = (value) => {
    if (typeof value === "number") {
      return Number.isFinite(value) ? value : null;
    }
    if (typeof value !== "string") {
      return null;
    }
    const cleaned = value.replace(/[^0-9.-]+/g, "");
    if (!cleaned) {
      return null;
    }
    const parsed = Number(cleaned);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const formatNumericTotal = (sum, sample) => {
    if (!Number.isFinite(sum)) {
      return "—";
    }
    if (typeof sample === "string") {
      if (/^\s*\$/.test(sample)) {
        return `$${sum.toLocaleString()}`;
      }
      if (sample.trim().endsWith("%")) {
        return `${sum.toFixed(2)}%`;
      }
    }
    return sum % 1 === 0
      ? sum.toLocaleString()
      : sum.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  return columns.reduce((acc, column, columnIndex) => {
    const values = rows.map((row) => row[columnIndex]);
    const numericValues = values
      .map((value) => parseNumericValue(value))
      .filter((value) => value !== null);

    if (numericValues.length > 0) {
      const sum = numericValues.reduce((total, value) => total + value, 0);
      acc[column] = formatNumericTotal(sum, values[0]);
    }

    return acc;
  }, {});
};

const TaskContentPanel = ({
  task,
  uploadedFile,
  selectedSheets,
  isUploadReady,
  isAnalyzing,
  progressMsg,
  progressSteps = [],
  onTaskPromptChange,
  onSuggestionSelect,
  onAnalyzeTask,
  onTaskPageSizeChange,
  onTaskPageChange,
  onStepPage,
  onExportPreview,
  onTaskChartTypeChange,
  onTaskChartPanelToggle,
  onTaskChartDataPageSizeChange,  // 添加缺失的 prop
  onTaskChartDataPageChange,      // 添加缺失的 prop
  onExportChartImage,
  onExportChartData,
  exportMenuTaskId,
  onToggleExportMenu,
  chartContainerRefs,
  onUpdateTask,
}) => {
  const summaryBanner = useMemo(() => {
    // 检查文件是否存在（使用 fileId 或 id）
    const hasFile = uploadedFile?.fileId || uploadedFile?.id;
    if (!hasFile) {
      return {
        variant: "info",
        title: "Upload Required",
        subtitle: "Upload Excel files and select sheets to start using AI analysis features.",
      };
    }

    if (selectedSheets.length === 0) {
      return {
        variant: "warning",
        title: "Select Sheets",
        subtitle: "Select one or more sheets in the upload area to enable AI analysis.",
      };
    }

    return {
      variant: "success",
      title: "Ready for Analysis",
      subtitle: `${selectedSheets.length} ${selectedSheets.length === 1 ? "sheet" : "sheets"} selected`,
    };
  }, [uploadedFile?.fileId, selectedSheets.length]);

  const bannerStyles = useMemo(() => {
    switch (summaryBanner.variant) {
      case "success":
        return {
          container: "bg-green-50 border-green-200",
          icon: "text-green-500",
        };
      case "warning":
        return {
          container: "bg-yellow-50 border-yellow-200",
          icon: "text-orange-500",
        };
      default:
        return {
          container: "bg-blue-50 border-blue-200",
          icon: "text-blue-500",
        };
    }
  }, [summaryBanner.variant]);

  if (!task) {
    return (
      <div className="flex-1 flex items-center justify-center bg-white">
        <div className="text-center text-gray-500">
          <p className="text-lg mb-2">No task selected</p>
          <p className="text-sm">Select a task from the left or create a new one</p>
        </div>
      </div>
    );
  }

  const pendingTask = task && task.status !== "completed";
  const hasResults = task && task.results && task.results.length > 0;
  const currentProgressIndex = inferProgressStage(progressMsg);
  const visibleProgressSteps = progressSteps.slice(-5);

  const renderAnalysisProgress = () => (
    <div className="border border-blue-100 bg-blue-50 rounded-lg p-4 space-y-4">
      <Loader label={progressMsg || "正在分析数据..."} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {PROGRESS_STAGES.map((stage, index) => {
          const isDone = index < currentProgressIndex;
          const isActive = index === currentProgressIndex;
          const Icon = isDone ? CheckCircle2 : Circle;
          return (
            <div
              key={stage.key}
              className={`h-9 px-2 rounded-md border flex items-center gap-1.5 text-xs ${
                isActive
                  ? "bg-white border-blue-300 text-blue-700 shadow-sm"
                  : isDone
                    ? "bg-white/70 border-blue-100 text-blue-600"
                    : "bg-blue-50 border-blue-100 text-blue-300"
              }`}
            >
              <Icon className={`w-3.5 h-3.5 ${isActive ? "animate-pulse" : ""}`} />
              <span className="truncate">{stage.label}</span>
            </div>
          );
        })}
      </div>
      {visibleProgressSteps.length > 0 && (
        <div className="space-y-1">
          {visibleProgressSteps.map((step, index) => (
            <div key={`${step.at}-${index}`} className="flex items-center gap-2 text-xs text-slate-500">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-300" />
              <span>{step.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  const renderSingleResult = (result, resultIndex) => {
    const {
      type, // "data" | "chart" | "both" | "insights-only"
      preview: primaryPreview,
      previews = [],
      suggestion,
      processedAt,
      chartData: primaryChartData,
      chartDatas = [],
      runtimeNote,
      metrics = [],
    } = result;

    // 根据 type 决定显示什么内容
    const showData = type === "data" || type === "both";
    const showChart = type === "chart" || type === "both";
    const showSuggestion = suggestion && suggestion.trim();

    const availablePreviews = previews.length > 0
      ? previews
      : primaryPreview
        ? [primaryPreview]
        : [];
    const activePreviewIndex = Math.min(
      Math.max(task.activePreviewIndex ?? 0, 0),
      Math.max(availablePreviews.length - 1, 0)
    );
    const preview = availablePreviews[activePreviewIndex] ?? null;

    const previewColumns = preview?.columns ?? [];
    const previewRowsRaw = preview?.rows ?? [];

    // 将对象数组转换为二维数组
    // 后端返回：[{col1: val1, col2: val2}, ...]
    // 前端需要：[[val1, val2], ...]
    const previewRows = previewRowsRaw.map(rowObj => {
      // 如果已经是数组，直接返回
      if (Array.isArray(rowObj)) {
        return rowObj;
      }
      // 如果是对象，按列顺序转换为数组
      return previewColumns.map(col => {
        const value = rowObj[col];
        // 处理 null/undefined
        if (value === null || value === undefined) {
          return "—";
        }
        // 处理数字格式化
        if (typeof value === 'number') {
          return value.toLocaleString();
        }
        return String(value);
      });
    });

    const totalsFromResult = preview?.totals ?? {};
    const totalRowsProcessed = preview?.totalRowCount ?? previewRows.length;
    const isPreviewTruncated = totalRowsProcessed > previewRows.length;

    const rowsPerPage = task.pageSize ?? 10;
    const totalPages = Math.max(1, Math.ceil(previewRows.length / rowsPerPage));
    const safeCurrentPage = Math.min(task.currentPage ?? 1, totalPages);
    const startIndex = (safeCurrentPage - 1) * rowsPerPage;
    const paginatedRows = previewRows.slice(startIndex, startIndex + rowsPerPage);
    const paginationRange = buildPaginationRange(safeCurrentPage, totalPages, 2);
    const totalsRowLabel = `Totals (${previewRows.length.toLocaleString()} preview rows)`;
    const effectiveTotals =
      Object.keys(totalsFromResult).length > 0
        ? totalsFromResult
        : buildPreviewTotals(previewColumns, previewRows);

    const availableCharts = chartDatas.length > 0
      ? chartDatas
      : primaryChartData
        ? [primaryChartData]
        : [];
    const activeChartIndex = Math.min(
      Math.max(task.activeChartIndex ?? 0, 0),
      Math.max(availableCharts.length - 1, 0)
    );
    const chartData = availableCharts[activeChartIndex] ?? null;

    const defaultChartType = CHART_TYPES.includes(chartData?.defaultType)
      ? chartData.defaultType
      : CHART_TYPES[0];
    const activeChartType = CHART_TYPES.includes(task.selectedChartType)
      ? task.selectedChartType
      : defaultChartType;
    const isChartDataView = Boolean(task.isChartDataView);
    const activeVisualizationMode = isChartDataView ? "data" : activeChartType;
    const chartDataRows = buildChartDataRows(chartData);

    return (
      <div className="bg-white rounded-xl shadow-sm hover:shadow transition-shadow px-3 pt-2.5 pb-4 sm:px-6 sm:pt-2.5 sm:pb-6 lg:px-8 lg:pt-2.5 lg:pb-8">
        {metrics.length > 0 && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 mb-6">
            {metrics.map((metric) => (
              <div key={`${metric.label}-${metric.value}`} className="border-l-2 border-blue-500 pl-3 py-1">
                <p className="text-xs text-slate-500">{metric.label}</p>
                <p className="text-xl font-semibold text-slate-900">
                  {formatNumber(metric.value)}{metric.unit ? ` ${metric.unit}` : ""}
                </p>
              </div>
            ))}
          </div>
        )}
        {/* Data Processing / Both: 展示数据表 */}
        {showData && previewColumns.length > 0 && (
          <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden mb-6">
              {availablePreviews.length > 1 && (
                <div
                  role="tablist"
                  aria-label="分析结果"
                  className="flex overflow-x-auto border-b border-slate-200 bg-slate-50 px-3"
                >
                  {availablePreviews.map((item, index) => {
                    const isActive = index === activePreviewIndex;
                    return (
                      <button
                        key={`${item.title}-${index}`}
                        type="button"
                        role="tab"
                        aria-selected={isActive}
                        title={item.title}
                        onClick={() => onUpdateTask?.(
                          task.id,
                          () => ({ activePreviewIndex: index, currentPage: 1 })
                        )}
                        className={`min-w-0 max-w-56 shrink-0 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset ${
                          isActive
                            ? "border-blue-600 bg-white text-blue-700"
                            : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"
                        }`}
                      >
                        <span className="block truncate">{item.title}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              <div className="px-4 py-3 border-b border-slate-100 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-col gap-1">
                  <h5 className="text-sm font-semibold text-gray-900">
                    {availablePreviews.length > 1 ? preview.title : "Data Preview"}
                  </h5>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-gray-600">
                    <span>Total Rows Processed: {totalRowsProcessed.toLocaleString()}</span>
                    {isPreviewTruncated && (
                      <span className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5 text-amber-700 border border-amber-100">
                        <Database className="w-3 h-3" />
                        Showing {previewRows.length.toLocaleString()} preview rows
                      </span>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => onExportPreview(task.id)}
                  className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 border border-slate-200 rounded-md transition-colors"
                >
                  <Download className="w-4 h-4" />
                  Export
                </button>
              </div>
              {((availablePreviews.length === 1 && runtimeNote) || isPreviewTruncated) && (
                <div className="px-4 py-2 border-b border-amber-100 bg-amber-50 text-xs text-amber-800">
                  {(availablePreviews.length === 1 && runtimeNote) || "后端会按全量数据执行分析，前端表格只展示预览行以保持页面流畅。"}
                </div>
              )}
              <div className="overflow-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm whitespace-nowrap relative">
                  <thead className="bg-slate-50 sticky top-0 z-10">
                    <tr>
                      {previewColumns.map((column) => (
                        <th
                          key={column}
                          className="px-4 py-2 text-left text-xs font-semibold text-gray-900 uppercase tracking-wide align-top"
                        >
                          <span className="truncate">{column}</span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="bg-white divide-y divide-slate-100">
                    {paginatedRows.map((row, rowIndex) => (
                      <tr key={`row-${rowIndex}`}>
                        {row.map((cell, cellIndex) => (
                          <td key={`${rowIndex}-${cellIndex}`} className="px-4 py-2 text-slate-700">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                  {previewColumns.length > 0 && (
                    <tfoot className="bg-slate-100 sticky bottom-0 z-10 border-t border-slate-200">
                      <tr>
                        {previewColumns.map((column, columnIndex) => (
                          <td
                            key={`totals-${column}`}
                            className="px-4 py-2 font-semibold text-gray-900"
                          >
                            {columnIndex === 0 ? totalsRowLabel : effectiveTotals[column] ?? "—"}
                          </td>
                        ))}
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>

              <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 px-4 py-3 border-t border-slate-100">
                <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                  <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                    <span>Page size</span>
                    <select
                      value={rowsPerPage}
                      onChange={(event) =>
                        onTaskPageSizeChange(task.id, Number(event.target.value))
                      }
                      className="px-2 py-1 text-xs border border-slate-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                    >
                      <option value={10}>10</option>
                      <option value={20}>20</option>
                      <option value={50}>50</option>
                    </select>
                  </label>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => onStepPage(task.id, -1)}
                    disabled={safeCurrentPage === 1}
                    className="inline-flex items-center justify-center w-8 h-8 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed"
                    aria-label="Previous page"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  {paginationRange.map((item, index) =>
                    typeof item === "string" ? (
                      <span key={`${item}-${index}`} className="px-2 text-xs font-medium text-slate-400">
                        ...
                      </span>
                    ) : (
                      <button
                        key={item}
                        type="button"
                        onClick={() => onTaskPageChange(task.id, item)}
                        className={`min-w-[2rem] h-8 px-2 text-xs font-medium rounded-md border ${
                          item === safeCurrentPage
                            ? "border-blue-500 bg-blue-50 text-blue-600"
                            : "border-slate-300 text-slate-600 hover:bg-slate-100"
                        }`}
                      >
                        {item}
                      </button>
                    )
                  )}
                  <button
                    type="button"
                    onClick={() => onStepPage(task.id, 1)}
                    disabled={safeCurrentPage >= totalPages}
                    className="inline-flex items-center justify-center w-8 h-8 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed"
                    aria-label="Next page"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          )}

        {/* Visualization / Both: 展示图表 */}
        {showChart && chartData && (
          <div className="bg-white border border-slate-200 rounded-lg shadow-sm overflow-hidden mb-6">
            {availableCharts.length > 1 && (
              <div
                role="tablist"
                aria-label="分析图表"
                className="flex overflow-x-auto border-b border-slate-200 bg-slate-50 px-3"
              >
                {availableCharts.map((item, index) => {
                  const isActive = index === activeChartIndex;
                  return (
                    <button
                      key={`${item.title}-${index}`}
                      type="button"
                      role="tab"
                      aria-selected={isActive}
                      title={item.title}
                      onClick={() => onUpdateTask?.(
                        task.id,
                        () => ({ activeChartIndex: index, chartDataPage: 1 })
                      )}
                      className={`min-w-0 max-w-56 shrink-0 border-b-2 px-3 py-2 text-xs font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-inset ${
                        isActive
                          ? "border-blue-600 bg-white text-blue-700"
                          : "border-transparent text-slate-500 hover:border-slate-300 hover:text-slate-800"
                      }`}
                    >
                      <span className="block truncate">{item.title}</span>
                    </button>
                  );
                })}
              </div>
            )}
            <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
              <h5 className="text-sm font-semibold text-gray-900">
                {availableCharts.length > 1 ? chartData.title : "Visualization"}
              </h5>
              <div className="flex items-center gap-2">
                <div className="flex items-center">
                  {VISUALIZATION_MODES.map((mode, index) => {
                    const isActive = activeVisualizationMode === mode;
                    return (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => {
                          if (mode === "data") {
                            onTaskChartPanelToggle(task.id, "data");
                          } else {
                            onTaskChartTypeChange(task.id, mode);
                          }
                        }}
                        className={`h-8 w-12 text-xs font-medium tracking-wide transition-colors ${
                          index === 0 ? "rounded-l" : index === VISUALIZATION_MODES.length - 1 ? "rounded-r" : ""
                        } ${
                          isActive
                            ? "bg-blue-600 text-white shadow-sm"
                            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                        }`}
                        style={{ marginLeft: index === 0 ? 0 : "-1px" }}
                      >
                        {VISUALIZATION_LABELS[mode]}
                      </button>
                    );
                  })}
                </div>
                <div className="relative" onClick={(event) => event.stopPropagation()}>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onToggleExportMenu(task.id);
                    }}
                    className="inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-slate-200 border border-slate-200 rounded-md transition-colors"
                    aria-label="Export results"
                  >
                    <Download className="w-4 h-4" />
                    Export
                  </button>
                  {exportMenuTaskId === task.id && (
                    <div className="absolute right-0 mt-2 w-36 rounded-md border border-slate-200 bg-white shadow-lg z-20">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onExportChartImage(task.id, resultIndex);
                          onToggleExportMenu(null);
                        }}
                        className="w-full px-3 py-2 text-left text-xs font-medium text-slate-600 hover:bg-slate-100 flex items-center gap-2"
                      >
                        <ImageDown className="w-3.5 h-3.5 text-slate-500" />
                        Export Image
                      </button>
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onExportChartData(task.id, resultIndex);
                          onToggleExportMenu(null);
                        }}
                        className="w-full px-3 py-2 text-left text-xs font-medium text-slate-600 hover:bg-slate-100 flex items-center gap-2"
                      >
                        <Download className="w-3.5 h-3.5 text-slate-500" />
                        Export Data
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="px-4 py-4 flex justify-center">
              <div className="w-full max-w-3xl">
                {isChartDataView ? (
                  <>
                  {/* 数据视图：隐藏渲染图表供 Export Image 使用 */}
                  <div
                    ref={(el) => { if (chartContainerRefs?.current) chartContainerRefs.current[`${task.id}-${resultIndex}`] = el; }}
                    className="w-full"
                    style={{ position: "absolute", left: -9999, opacity: 0, pointerEvents: "none", width: 800 }}
                  >
                    <ChartView
                      chartData={chartData}
                      chartType={activeChartType}
                      palette={chartData?.palette}
                    />
                  </div>
                  <div className="border border-slate-200 rounded-lg">
                    <div className="overflow-auto">
                      <table className="min-w-full divide-y divide-slate-200 text-xs">
                        <thead className="bg-slate-50 sticky top-0 z-10">
                          <tr>
                            <th className="px-3 py-2 text-left font-semibold text-slate-600">Label (X)</th>
                            {chartData?.series?.map((series) => (
                              <th key={series.name} className="px-3 py-2 text-left font-semibold text-slate-600">
                                {series.name} (Y)
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 bg-white">
                          {(() => {
                            const chartDataPage = task.chartDataPage || 1;
                            const chartDataPageSize = task.chartDataPageSize || 25;
                            const startIndex = (chartDataPage - 1) * chartDataPageSize;
                            const endIndex = startIndex + chartDataPageSize;
                            const paginatedChartRows = chartDataRows.slice(startIndex, endIndex);

                            return paginatedChartRows.map((row, idx) => (
                              <tr key={`${row.label}-${idx}`}>
                                <td className="px-3 py-2 text-slate-600">{row.label}</td>
                                {row.values.map((value, valueIndex) => (
                                  <td key={`${valueIndex}-${idx}`} className="px-3 py-2 text-slate-700 font-mono">
                                    {typeof value === 'number' ? value.toString() : formatNumber(value)}
                                  </td>
                                ))}
                              </tr>
                            ));
                          })()}
                        </tbody>
                      </table>
                    </div>

                    {/* Chart Data 分页控件 */}
                    <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 px-4 py-3 border-t border-slate-100 bg-slate-50">
                      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                        <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                          <span>Page size</span>
                          <select
                            value={task.chartDataPageSize || 25}
                            onChange={(event) =>
                              onTaskChartDataPageSizeChange?.(task.id, Number(event.target.value))
                            }
                            className="text-xs border-slate-200 rounded-md px-2 py-1"
                          >
                            <option value={10}>10</option>
                            <option value={25}>25</option>
                            <option value={50}>50</option>
                            <option value={100}>100</option>
                          </select>
                        </label>
                        <p className="text-xs text-slate-500">
                          Showing {Math.min((task.chartDataPage || 1) * (task.chartDataPageSize || 25), chartDataRows.length)} of {chartDataRows.length} data points
                        </p>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          disabled={(task.chartDataPage || 1) === 1}
                          onClick={() => onTaskChartDataPageChange?.(task.id, (task.chartDataPage || 1) - 1)}
                          className="p-1 rounded border border-slate-200 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <ChevronLeft className="w-4 h-4" />
                        </button>
                        <span className="text-xs text-slate-600 min-w-[80px] text-center">
                          Page {task.chartDataPage || 1} of {Math.ceil(chartDataRows.length / (task.chartDataPageSize || 25))}
                        </span>
                        <button
                          type="button"
                          disabled={(task.chartDataPage || 1) >= Math.ceil(chartDataRows.length / (task.chartDataPageSize || 25))}
                          onClick={() => onTaskChartDataPageChange?.(task.id, (task.chartDataPage || 1) + 1)}
                          className="p-1 rounded border border-slate-200 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                        >
                          <ChevronRight className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                  </>
                ) : (
                  <div
                    ref={(el) => { if (chartContainerRefs?.current) chartContainerRefs.current[`${task.id}-${resultIndex}`] = el; }}
                    className="w-full"
                  >
                    <ChartView
                      chartData={chartData}
                      chartType={activeChartType}
                      palette={chartData?.palette}
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Suggestion: 所有类型都显示（如果有） */}
        {showSuggestion && (
          <div className="bg-white border border-slate-200 rounded-lg shadow-sm p-4">
            <h6 className="text-sm font-semibold text-slate-700 mb-2 flex items-center gap-2">
              <Lightbulb className="w-4 h-4 text-yellow-500" />
              Suggestion
            </h6>
            <p className="text-sm text-slate-600 whitespace-pre-wrap">{suggestion}</p>
          </div>
        )}

        <div className="text-xs text-slate-400 mt-4">
          Completed at {new Date(processedAt || new Date()).toLocaleString()} · Type: {RESULT_TYPE_LABELS[type] || type || "unknown"}
        </div>
      </div>
    );
  };

  // 判断是否显示 Quick Suggestions（只在第一次，没有结果时显示）
  // 移除 parentTaskId 检查，每个task都是独立的
  const showQuickSuggestions = !hasResults && task.status !== "completed";

  return (
    <div className="flex flex-col bg-white min-h-0">
      <div className="flex-1 overflow-y-auto p-6">
        {/* 初始状态：显示 AI Data Query 标题和输入框 */}
        {!hasResults && (
          <>
            <div className="flex items-center gap-2 mb-6">
              <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center">
                <Brain className="w-5 h-5 text-purple-600" />
              </div>
              <h3 className="text-base sm:text-lg font-semibold text-gray-900">AI Data Query</h3>
            </div>

            <div className={`rounded-lg p-3 flex items-start gap-3 border ${bannerStyles.container} mb-6`}>
              <Info className={`w-5 h-5 flex-shrink-0 mt-0.5 ${bannerStyles.icon}`} />
              <div>
                <p className="text-sm font-semibold text-gray-800">{summaryBanner.title}</p>
                <p className="text-xs text-gray-600 mt-0.5">{summaryBanner.subtitle}</p>
              </div>
            </div>
          </>
        )}

        {/* 显示历史 query 和结果 */}
        {hasResults && (
          <div className="space-y-8">
            {task.results.map((result, resultIndex) => {
              const resultPrompt = result.prompt || "";
              return (
                <div key={resultIndex} className="space-y-6">
                  {/* 历史 Query 展示（只读） */}
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center">
                        <Brain className="w-5 h-5 text-purple-600" />
                      </div>
                      <h3 className="text-base sm:text-lg font-semibold text-gray-900">AI Data Query</h3>
                    </div>
                    <div className="relative">
                      <textarea
                        value={resultPrompt}
                        readOnly
                        disabled
                        className="w-full px-4 pt-3 pb-3 border border-gray-200 rounded-lg bg-gray-50 text-gray-700 cursor-default resize-none"
                        rows={Math.max(2, Math.ceil(resultPrompt.length / 60))}
                      />
                    </div>
                  </div>

                  {/* Analysis Results */}
                  <div className="space-y-2.5">
                    <div className="flex items-center gap-2">
                      <Brain className="w-5 h-5 text-purple-600" />
                      <h3 className="text-base sm:text-lg font-semibold text-gray-900">
                        Analysis Results {task.results.length > 1 ? `#${resultIndex + 1}` : ""}
                      </h3>
                    </div>

                    {renderSingleResult(result, resultIndex)}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* 输入框区域 */}
        {pendingTask && (
          <div className={`space-y-4 ${hasResults ? "mt-8" : ""}`}>
            {/* 移除 "Refining Task" 显示，每个task都是独立的 */}

            {hasResults && (
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center">
                  <Brain className="w-5 h-5 text-purple-600" />
                </div>
                <h3 className="text-base sm:text-lg font-semibold text-gray-900">AI Data Query</h3>
              </div>
            )}

            <div className="relative">
              <textarea
                value={task.prompt}
                onChange={(event) => onTaskPromptChange(task.id, event.target.value)}
                disabled={!isUploadReady || isAnalyzing}
                placeholder="Describe what you need from your data..."
                className="w-full px-4 pt-3 pb-12 pr-32 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none disabled:bg-gray-100 disabled:text-gray-400"
                rows={3}
              />
              <div className="absolute bottom-3 right-4 flex items-center gap-3">
                <span className="text-xs text-gray-400">
                  {task.prompt.length}/{maxChars}
                </span>
                <button
                  type="button"
                  onClick={() => onAnalyzeTask(task.id)}
                  disabled={!isUploadReady || isAnalyzing || !task.prompt?.trim()}
                  className="px-4 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed text-white text-sm font-medium rounded transition-colors"
                >
                  {isAnalyzing && task.status === "running" ? "Analyzing..." : "Analyze"}
                </button>
              </div>
            </div>

            {task.analysisError && !isAnalyzing && (
              <div className="border border-red-200 bg-red-50 rounded-lg p-4 flex items-start gap-3">
                <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="text-sm font-semibold text-red-900">分析未完成</p>
                  <p className="text-sm text-red-700">{task.analysisError}</p>
                </div>
              </div>
            )}

            {/* Quick Suggestions - 只在第一次显示 */}
            {showQuickSuggestions && (
              <div>
                <p className="text-sm text-gray-900 mb-3">Quick Suggestions</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {QUICK_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      onClick={() => onSuggestionSelect(task.id, suggestion)}
                      disabled={!isUploadReady || isAnalyzing}
                      className="px-4 py-2 text-sm text-left border border-gray-200 rounded-lg hover:bg-gray-50 hover:border-blue-300 transition-colors flex items-center gap-2 disabled:text-gray-400 disabled:border-gray-200"
                    >
                      <Lightbulb className="w-4 h-4 text-blue-400 flex-shrink-0" />
                      <span className="text-gray-600">{suggestion}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {isAnalyzing && task.status === "running" && (
              renderAnalysisProgress()
            )}
          </div>
        )}

        {!pendingTask && (
          <div className="relative">
            <textarea
              value={task?.prompt || ""}
              onChange={(event) => task && onTaskPromptChange(task.id, event.target.value)}
              disabled={!isUploadReady || isAnalyzing || !task}
              placeholder="Describe what you need from your data..."
              className="w-full px-4 pt-3 pb-12 pr-32 border border-gray-300 rounded-lg bg-gray-100 text-gray-400 cursor-not-allowed focus:outline-none resize-none"
              rows={3}
            />
            <div className="absolute bottom-3 right-4 flex items-center gap-3">
              <span className="text-xs text-gray-400">
                {task?.prompt?.length || 0}/{maxChars}
              </span>
              <button
                type="button"
                disabled
                className="px-4 py-1.5 bg-gray-300 text-white text-sm font-medium rounded cursor-not-allowed"
              >
                Analyze
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default TaskContentPanel;

import React, { lazy, Suspense, useEffect, useMemo, useRef } from "react";
import {
  AlertCircle,
  ArrowUp,
  CheckCircle2,
  Circle,
  Database,
  Info,
  Download,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Columns3,
  ImageDown,
  Calculator,
  LoaderCircle,
} from "lucide-react";
import Loader from "../Loader";
import {
  CHART_DISPLAY_LIMITS,
  CHART_TYPES,
  DEFAULT_CHART_DISPLAY_LIMIT,
  VISUALIZATION_MODES,
  VISUALIZATION_LABELS,
} from "../../constants/chart";
import {
  buildChartDisplayData,
  chartDisplayMessage,
} from "../../utils/chartDisplay";
import {
  ANALYSIS_QUERY_COUNTER_THRESHOLD,
  MAX_ANALYSIS_QUERY_LENGTH,
} from "../../constants/analysis";
import { hasExpandableResultContent } from "../../utils/conversationDisplay";

const RechartsVisualization = lazy(() => import("./RechartsVisualization"));
const AnalysisMarkdown = lazy(() => import("./AnalysisMarkdown"));

const ChartView = (props) => (
  <Suspense fallback={<Loader label="Loading chart..." />}>
    <RechartsVisualization {...props} />
  </Suspense>
);

const AnswerText = ({ children }) => (
  <Suspense fallback={<p className="whitespace-pre-wrap break-words text-[15px] leading-6 text-slate-700">{children}</p>}>
    <AnalysisMarkdown>{children}</AnalysisMarkdown>
  </Suspense>
);

const ResultStatusMessages = ({ items = [], className = "" }) => (
  <div className={`space-y-2 ${className}`}>
    {items.map((item, index) => {
      const isError = item.status === "failed";
      const Icon = isError ? AlertCircle : Info;
      return (
        <div
          key={`${item.error_code || item.status}-${index}`}
          role={isError ? "alert" : "status"}
          className={`sm-status ${isError ? "sm-status-error" : "sm-status-warning"}`}
        >
          <Icon className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="min-w-0">
            <h6 className="text-sm font-semibold">
              {isError ? "Analysis failed" : "No results found"}
            </h6>
            <p className="mt-0.5 text-sm leading-6">{item.message}</p>
          </div>
        </div>
      );
    })}
  </div>
);

const PROGRESS_STAGES = [
  { key: "thinking", label: "Understand" },
  { key: "select", label: "Select data" },
  { key: "load", label: "Load Excel" },
  { key: "execute", label: "Analyze" },
  { key: "chart", label: "Build chart" },
  { key: "insight", label: "Write insight" },
];

const inferProgressStage = (message = "") => {
  const lower = message.toLowerCase();
  if (message.includes("选择") || lower.includes("select")) return 1;
  if (message.includes("加载") || lower.includes("load")) return 2;
  if (
    message.includes("筛选")
    || message.includes("执行")
    || message.includes("分析数据")
    || message.includes("代码")
    || lower.includes("query")
    || lower.includes("execute")
    || lower.includes("analy")
    || lower.includes("code")
  ) return 3;
  if (message.includes("图表") || message.includes("图") || lower.includes("chart")) return 4;
  if (message.includes("洞察") || message.includes("总结") || lower.includes("insight") || lower.includes("summary")) return 5;
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

const resizePromptInput = (input) => {
  if (!input) return;
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
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
  exportMenuKey,
  onToggleExportMenu,
  chartContainerRefs,
  onUpdateTask,
}) => {
  const promptInputRef = useRef(null);
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
          container: "sm-status-info",
          icon: "text-blue-600",
        };
      case "warning":
        return {
          container: "sm-status-warning",
          icon: "text-amber-600",
        };
      default:
        return {
          container: "sm-status-info",
          icon: "text-blue-600",
        };
    }
  }, [summaryBanner.variant]);

  useEffect(() => {
    resizePromptInput(promptInputRef.current);
  }, [task?.prompt]);

  if (!task) {
    return (
      <div className="flex-1 flex items-center justify-center bg-white">
        <div className="text-center text-slate-500">
          <p className="text-lg mb-2">No task selected</p>
          <p className="text-sm">Select a task from the left or create a new one</p>
        </div>
      </div>
    );
  }

  const pendingTask = task && task.status !== "completed";
  const hasResults = task && task.results && task.results.length > 0;
  const promptLength = (task.prompt || "").length;
  const showPromptCounter = promptLength >= ANALYSIS_QUERY_COUNTER_THRESHOLD;
  const currentProgressIndex = inferProgressStage(progressMsg);
  const visibleProgressSteps = progressSteps.slice(-5);

  const renderAnalysisProgress = () => (
    <div className="space-y-4 rounded-lg bg-blue-50 p-4">
      <Loader label={progressMsg || "Analyzing data..."} />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        {PROGRESS_STAGES.map((stage, index) => {
          const isDone = index < currentProgressIndex;
          const isActive = index === currentProgressIndex;
          const Icon = isDone ? CheckCircle2 : Circle;
          return (
            <div
              key={stage.key}
              className={`flex h-9 items-center gap-1.5 rounded-md px-2 text-xs ${
                isActive
                  ? "bg-white text-blue-700"
                  : isDone
                    ? "bg-blue-100/70 text-blue-600"
                    : "text-blue-300"
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

  const renderSingleResult = (result, resultIndex, options = {}) => {
    const { hideSummary = false, completedLabel = "" } = options;
    const {
      type, // "data" | "chart" | "both" | "insight_only" | "clarification"
      preview: primaryPreview,
      previews = [],
      suggestion,
      chartData: primaryChartData,
      chartDatas = [],
      runtimeNote,
      metrics = [],
      fieldResolutions = [],
      sheetResolutions = [],
      summaryItems = [],
      statusMessages = [],
    } = result;
    const uiState = result.uiState ?? {};
    const updateResultUi = (patch) => onUpdateTask?.(
      task.id,
      (currentTask) => ({
        results: (currentTask.results ?? []).map((item, index) =>
          index === resultIndex
            ? { ...item, uiState: { ...(item.uiState ?? {}), ...patch } }
            : item
        ),
      })
    );

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
      Math.max(uiState.activePreviewIndex ?? 0, 0),
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

    const rowsPerPage = uiState.pageSize ?? 10;
    const totalPages = Math.max(1, Math.ceil(previewRows.length / rowsPerPage));
    const safeCurrentPage = Math.min(uiState.currentPage ?? 1, totalPages);
    const startIndex = (safeCurrentPage - 1) * rowsPerPage;
    const paginatedRows = previewRows.slice(startIndex, startIndex + rowsPerPage);
    const paginationRange = buildPaginationRange(safeCurrentPage, totalPages, 2);
    const totalsRowLabel = `Totals (${previewRows.length.toLocaleString()} preview rows)`;
    const effectiveTotals =
      Object.keys(totalsFromResult).length > 0
        ? totalsFromResult
        : buildPreviewTotals(previewColumns, previewRows);
    const numericColumns = new Set(Object.keys(effectiveTotals));

    const availableCharts = chartDatas.length > 0
      ? chartDatas
      : primaryChartData
        ? [primaryChartData]
        : [];
    const activeChartIndex = Math.min(
      Math.max(uiState.activeChartIndex ?? 0, 0),
      Math.max(availableCharts.length - 1, 0)
    );
    const chartData = availableCharts[activeChartIndex] ?? null;

    const defaultChartType = CHART_TYPES.includes(chartData?.defaultType)
      ? chartData.defaultType
      : CHART_TYPES[0];
    const activeChartType = CHART_TYPES.includes(uiState.selectedChartType)
      ? uiState.selectedChartType
      : defaultChartType;
    const isChartDataView = Boolean(uiState.isChartDataView);
    const activeVisualizationMode = isChartDataView ? "data" : activeChartType;
    const chartDataRows = buildChartDataRows(chartData);
    const chartDataPageSize = uiState.chartDataPageSize || 25;
    const chartDataTotalPages = Math.max(
      1,
      Math.ceil(chartDataRows.length / chartDataPageSize)
    );
    const chartDataPage = Math.min(
      Math.max(uiState.chartDataPage || 1, 1),
      chartDataTotalPages
    );
    const chartDataStartIndex = (chartDataPage - 1) * chartDataPageSize;
    const paginatedChartRows = chartDataRows.slice(
      chartDataStartIndex,
      chartDataStartIndex + chartDataPageSize
    );
    const showChartDataPagination = chartDataTotalPages > 1;
    const chartXAxisLabel = chartData?.xAxisLabel?.trim() || "Category";
    const chartDisplayState = buildChartDisplayData(
      chartData,
      uiState.chartDisplayLimit ?? DEFAULT_CHART_DISPLAY_LIMIT
    );
    const displayedChartData = chartDisplayState.chartData;
    const displayMessage = chartDisplayMessage(chartDisplayState);
    const showDisplayLimit = (
      !isChartDataView
      && chartDisplayState.supportsTopN
      && chartDisplayState.totalCount > DEFAULT_CHART_DISPLAY_LIMIT
    );
    const displayedCategoryCount = displayedChartData?.labels?.length ?? 0;
    const chartMinWidth = displayedCategoryCount > 12
      ? Math.min(displayedCategoryCount * 52, 12000)
      : undefined;
    const currentExportMenuKey = `${task.id}-${resultIndex}`;

    return (
      <div>
        {!hideSummary && statusMessages.length > 0 && (
          <ResultStatusMessages items={statusMessages} className="mb-4" />
        )}
        {!hideSummary && (summaryItems.length > 0 ? summaryItems.map((item) => (
          <div key={`${item.questionId}-${item.query}`} className="mb-4">
            {summaryItems.length > 1 && (
              <h6 className="mb-1 text-sm font-semibold text-slate-900">{item.query}</h6>
            )}
            <AnswerText>{item.content}</AnswerText>
          </div>
        )) : showSuggestion && (
          <div className="mb-4">
            <AnswerText>{suggestion}</AnswerText>
          </div>
        ))}
        {!hideSummary && completedLabel && (
          <time className="mb-4 block text-xs text-slate-400">{completedLabel}</time>
        )}
        {metrics.length > 0 && (
          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {metrics.map((metric) => (
              <div key={`${metric.label}-${metric.value}`} className="border-l-2 border-blue-500 py-1 pl-3">
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
          <div className="mb-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
              {availablePreviews.length > 1 && (
                <div
                  role="tablist"
                  aria-label="Analysis results"
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
                        onClick={() => updateResultUi({ activePreviewIndex: index, currentPage: 1 })}
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
              <div className="flex flex-col gap-2 bg-slate-50/60 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-col gap-1">
                  <h5 className="text-sm font-semibold text-slate-900">
                    {preview.title || "Data Preview"}
                  </h5>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
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
                  onClick={() => onExportPreview(task.id, resultIndex, activePreviewIndex)}
                  className="sm-control-secondary inline-flex items-center justify-center gap-2"
                >
                  <Download className="w-4 h-4" />
                  Export Excel
                </button>
              </div>
              {((availablePreviews.length === 1 && runtimeNote) || isPreviewTruncated) && (
                <div className="bg-amber-50 px-4 py-2 text-xs leading-5 text-amber-800">
                  {(availablePreviews.length === 1 && runtimeNote) || "The backend analyzes the full dataset. This table shows preview rows to keep the page responsive."}
                </div>
              )}
              {preview.calculationBasis?.summary && (
                <details className="group bg-slate-50/70 px-4 py-3">
                  <summary className="flex cursor-pointer list-none items-start gap-2 text-xs text-slate-700 marker:content-none">
                    <Calculator className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
                    <span className="min-w-0 flex-1 leading-5">
                      <strong className="mr-1 font-semibold text-slate-900">Calculation basis</strong>
                      {preview.calculationBasis.summary}
                    </span>
                    <ChevronDown className="mt-0.5 h-4 w-4 shrink-0 text-slate-400 transition-transform group-open:rotate-180" />
                  </summary>
                  <dl className="mt-3 grid gap-2 pl-6 text-xs sm:grid-cols-[6rem_1fr]">
                    <dt className="font-medium text-slate-500">Source sheet</dt>
                    <dd className="break-words text-slate-700">{preview.calculationBasis.source_sheets?.join(", ") || "Selected sheets"}</dd>
                    <dt className="font-medium text-slate-500">Fields used</dt>
                    <dd className="break-words text-slate-700">{preview.calculationBasis.fields?.join(", ") || "Result fields"}</dd>
                    <dt className="font-medium text-slate-500">Method</dt>
                    <dd className="break-words text-slate-700">{preview.calculationBasis.operations?.join(", ") || "Data processing"}</dd>
                  </dl>
                </details>
              )}
              <div className="overflow-auto">
                <table className="min-w-full divide-y divide-slate-200 text-sm whitespace-nowrap relative">
                  <thead className="bg-slate-50 sticky top-0 z-10">
                    <tr>
                      {previewColumns.map((column) => (
                        <th
                          key={column}
                          className={`px-4 py-2.5 text-xs font-semibold text-slate-700 ${
                            numericColumns.has(column) ? "text-right" : "text-left"
                          }`}
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
                          <td
                            key={`${rowIndex}-${cellIndex}`}
                            className={`px-4 py-2.5 text-slate-700 ${
                              numericColumns.has(previewColumns[cellIndex]) ? "text-right tabular-nums" : "text-left"
                            }`}
                          >
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                  {previewColumns.length > 0 && Object.keys(effectiveTotals).length > 0 && (
                    <tfoot className="sticky bottom-0 z-10 border-t border-slate-200 bg-slate-50">
                      <tr>
                        {previewColumns.map((column, columnIndex) => (
                          <td
                            key={`totals-${column}`}
                            className={`px-4 py-2.5 font-semibold text-slate-900 ${
                              numericColumns.has(column) ? "text-right tabular-nums" : "text-left"
                            }`}
                          >
                            {previewColumns.length === 1
                              ? `Total: ${effectiveTotals[column] ?? "—"}`
                              : columnIndex === 0
                                ? totalsRowLabel
                                : effectiveTotals[column] ?? "—"}
                          </td>
                        ))}
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>

              {totalPages > 1 && (
              <div className="flex flex-col gap-3 border-t border-slate-100 bg-slate-50/60 px-4 py-3 md:flex-row md:items-center md:justify-between">
                <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                  <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                    <span>Page size</span>
                    <select
                      value={rowsPerPage}
                      onChange={(event) => updateResultUi({
                        pageSize: Number(event.target.value),
                        currentPage: 1,
                      })}
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
                    onClick={() => updateResultUi({ currentPage: Math.max(1, safeCurrentPage - 1) })}
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
                        onClick={() => updateResultUi({ currentPage: item })}
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
                    onClick={() => updateResultUi({ currentPage: Math.min(totalPages, safeCurrentPage + 1) })}
                    disabled={safeCurrentPage >= totalPages}
                    className="inline-flex items-center justify-center w-8 h-8 rounded-md border border-slate-300 text-slate-600 hover:bg-slate-100 disabled:opacity-50 disabled:cursor-not-allowed"
                    aria-label="Next page"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
              )}
            </div>
          )}

        {/* Visualization / Both: 展示图表 */}
        {showChart && chartData && (
          <div className="mb-6 overflow-hidden rounded-lg border border-slate-200/80 bg-white">
            {availableCharts.length > 1 && (
              <div
                role="tablist"
                aria-label="Analysis charts"
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
                      onClick={() => updateResultUi({ activeChartIndex: index, chartDataPage: 1 })}
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
            <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <h5 className="text-sm font-semibold text-slate-900">
                {availableCharts.length > 1 ? chartData.title : "Visualization"}
              </h5>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-2 sm:flex-none">
                  {showDisplayLimit && (
                    <select
                      aria-label="Categories shown"
                      value={uiState.chartDisplayLimit ?? DEFAULT_CHART_DISPLAY_LIMIT}
                      onChange={(event) => updateResultUi({
                        chartDisplayLimit: event.target.value === "all"
                          ? "all"
                          : Number(event.target.value),
                      })}
                      className="h-9 shrink-0 rounded-md border border-slate-200 bg-white px-2 text-sm font-medium text-slate-700 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                    >
                      {CHART_DISPLAY_LIMITS.map((limit) => (
                        <option key={limit} value={limit}>
                          {limit === "all" ? "All" : `Top ${limit}`}
                        </option>
                      ))}
                    </select>
                  )}
                  <div className="grid min-w-0 flex-1 grid-cols-4 sm:flex sm:flex-none">
                  {VISUALIZATION_MODES.map((mode, index) => {
                    const isActive = activeVisualizationMode === mode;
                    return (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => {
                          if (mode === "data") {
                            updateResultUi({ isChartDataView: true });
                          } else {
                            updateResultUi({ selectedChartType: mode, isChartDataView: false });
                          }
                        }}
                        className={`h-9 min-w-0 px-2 text-sm font-medium transition-colors sm:w-12 ${
                          index === 0 ? "rounded-l" : index === VISUALIZATION_MODES.length - 1 ? "rounded-r" : ""
                        } ${
                          isActive
                            ? "bg-blue-600 text-white"
                            : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                        }`}
                        style={{ marginLeft: index === 0 ? 0 : "-1px" }}
                      >
                        {VISUALIZATION_LABELS[mode]}
                      </button>
                    );
                  })}
                  </div>
                </div>
                <div className="relative" onClick={(event) => event.stopPropagation()}>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onToggleExportMenu(currentExportMenuKey);
                    }}
                    className="sm-control-secondary inline-flex h-9 items-center gap-2"
                    aria-label="Export results"
                  >
                    <Download className="w-4 h-4" />
                    Export
                  </button>
                  {exportMenuKey === currentExportMenuKey && (
                    <div className="absolute right-0 mt-2 w-40 rounded-md border border-slate-200 bg-white shadow-lg z-20">
                      {!isChartDataView && (
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            onExportChartImage(task.id, resultIndex, activeChartIndex);
                            onToggleExportMenu(null);
                          }}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-slate-600 hover:bg-slate-100"
                        >
                          <ImageDown className="w-3.5 h-3.5 text-slate-500" />
                          Export PNG
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onExportChartData(task.id, resultIndex, activeChartIndex);
                          onToggleExportMenu(null);
                        }}
                        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-slate-600 hover:bg-slate-100"
                      >
                        <Download className="w-3.5 h-3.5 text-slate-500" />
                        Export Excel
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>

            {!isChartDataView && displayMessage && (
              <div
                role="note"
                className="flex items-start gap-2 bg-blue-50 px-4 py-2.5 text-xs leading-5 text-blue-900"
              >
                <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
                <span>{displayMessage}</span>
              </div>
            )}

            <div className="overflow-x-auto px-4 py-4">
              <div
                className={isChartDataView ? "w-full" : "mx-auto w-full max-w-3xl"}
                style={{ minWidth: isChartDataView ? undefined : chartMinWidth }}
              >
                {isChartDataView ? (
                  <div className="overflow-hidden rounded-md">
                    <div className="overflow-auto">
                      <table className="min-w-full divide-y divide-slate-200 text-sm">
                        <thead className="bg-slate-50 sticky top-0 z-10">
                          <tr>
                            <th className="px-3 py-2 text-left font-semibold text-slate-600">
                              {chartXAxisLabel}
                            </th>
                            {chartData?.series?.map((series) => (
                              <th key={series.name} className="px-3 py-2 text-right font-semibold text-slate-600">
                                {series.name || chartData?.yAxisLabel || "Value"}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100 bg-white">
                          {paginatedChartRows.map((row, idx) => (
                            <tr key={`${row.label}-${idx}`}>
                              <td className="px-3 py-2 text-slate-600">{row.label}</td>
                              {row.values.map((value, valueIndex) => (
                                <td
                                  key={`${valueIndex}-${idx}`}
                                  className="px-3 py-2 text-right font-mono tabular-nums text-slate-700"
                                >
                                  {formatNumber(value)}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>

                    {showChartDataPagination && (
                      <div className="flex flex-col gap-3 border-t border-slate-100 bg-slate-50 px-4 py-3 md:flex-row md:items-center md:justify-between">
                        <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                          <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                            <span>Page size</span>
                            <select
                              value={chartDataPageSize}
                              onChange={(event) => updateResultUi({
                                chartDataPageSize: Number(event.target.value),
                                chartDataPage: 1,
                              })}
                              className="text-xs border-slate-200 rounded-md px-2 py-1"
                            >
                              <option value={10}>10</option>
                              <option value={25}>25</option>
                              <option value={50}>50</option>
                              <option value={100}>100</option>
                            </select>
                          </label>
                          <p className="text-xs text-slate-500">
                            Showing {Math.min(chartDataPage * chartDataPageSize, chartDataRows.length)} of {chartDataRows.length} data points
                          </p>
                        </div>

                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            aria-label="Previous data page"
                            disabled={chartDataPage === 1}
                            onClick={() => updateResultUi({ chartDataPage: chartDataPage - 1 })}
                            className="p-1 rounded border border-slate-200 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            <ChevronLeft className="w-4 h-4" />
                          </button>
                          <span className="text-xs text-slate-600 min-w-[80px] text-center">
                            Page {chartDataPage} of {chartDataTotalPages}
                          </span>
                          <button
                            type="button"
                            aria-label="Next data page"
                            disabled={chartDataPage >= chartDataTotalPages}
                            onClick={() => updateResultUi({ chartDataPage: chartDataPage + 1 })}
                            className="p-1 rounded border border-slate-200 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
                          >
                            <ChevronRight className="w-4 h-4" />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div
                    ref={(el) => { if (chartContainerRefs?.current) chartContainerRefs.current[`${task.id}-${resultIndex}`] = el; }}
                    className="w-full"
                  >
                    <ChartView
                      chartData={displayedChartData}
                      chartType={activeChartType}
                      palette={displayedChartData?.palette}
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {fieldResolutions.length > 0 && (
          <div className="sm-status sm-status-warning mt-3">
            <div className="flex items-start gap-3">
              <Columns3 className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1 space-y-3">
                <h6 className="text-sm font-semibold text-amber-950">
                  {fieldResolutions.some((item) => item.status === "needs_clarification")
                    ? "Choose an analysis field"
                    : "Field usage note"}
                </h6>
                {fieldResolutions.map((resolution) => (
                  <div key={`${resolution.reference}-${resolution.status}`} className="space-y-2">
                    <p className="text-sm text-amber-900">{resolution.message}</p>
                    {resolution.status === "needs_clarification" && (
                      <div className="flex flex-wrap gap-2">
                        {(resolution.candidates || []).map((candidate) => (
                          <button
                            key={candidate.column}
                            type="button"
                            title={candidate.reason || `Use field ${candidate.column}`}
                            onClick={() => onTaskPromptChange(
                              task.id,
                              `Continue with column "${candidate.column}": ${result.prompt || resolution.reference}`
                            )}
                            className="inline-flex items-center gap-1.5 border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-950 hover:border-amber-500 hover:bg-amber-100 focus:outline-none focus:ring-2 focus:ring-amber-500"
                          >
                            <Columns3 className="w-3.5 h-3.5" />
                            <span className="break-all">{candidate.column}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {sheetResolutions.length > 0 && (
          <div className="sm-status sm-status-warning mt-3">
            <div className="flex items-start gap-3">
              <Database className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
              <div className="min-w-0 flex-1 space-y-3">
                <h6 className="text-sm font-semibold text-amber-950">Choose a data sheet</h6>
                {sheetResolutions.map((resolution, resolutionIndex) => (
                  <div key={`${resolution.status}-${resolutionIndex}`} className="space-y-2">
                    <p className="text-sm text-amber-900">{resolution.message}</p>
                    <div className="flex flex-wrap gap-2">
                      {(resolution.candidates || []).map((candidate) => (
                        <div
                          key={candidate.candidate_id}
                          title={candidate.reason || `${candidate.file_name} / ${candidate.sheet_name}`}
                          className="inline-flex max-w-full items-center gap-1.5 border border-amber-300 bg-white px-3 py-1.5 text-sm font-medium text-amber-950"
                        >
                          <Database className="w-3.5 h-3.5 flex-shrink-0" />
                          <span className="break-all">{candidate.file_name} / {candidate.sheet_name}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

      </div>
    );
  };

  const resultSummary = (result) => {
    let summary = "";
    if (result.summaryItems?.length) {
      summary = result.summaryItems.map((item) => item.content).filter(Boolean).join(" ");
    } else if (result.suggestion?.trim()) {
      summary = result.suggestion.trim();
    } else if (result.statusMessages?.length) {
      summary = result.statusMessages.map((item) => item.message).filter(Boolean).join(" ");
    }
    if (!summary) return "View this analysis run's data, charts, and calculation basis.";
    return summary;
  };

  const formatResultTime = (value) => {
    const date = value ? new Date(value) : null;
    if (!date || Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex-1 py-1">
        {!hasResults && summaryBanner.variant !== "success" && (
            <div className={`sm-status mb-5 ${bannerStyles.container}`}>
              <Info className={`w-5 h-5 flex-shrink-0 mt-0.5 ${bannerStyles.icon}`} />
              <div>
                <p className="text-sm font-semibold text-slate-800">{summaryBanner.title}</p>
                <p className="text-xs text-slate-600 mt-0.5">{summaryBanner.subtitle}</p>
              </div>
            </div>
        )}

        {hasResults && (
          <div className="space-y-5">
            {task.results.map((result, resultIndex) => {
              const resultPrompt = result.prompt || "";
              const isLatestResult = resultIndex === task.results.length - 1;
              const hasDetails = hasExpandableResultContent(result);
              const hasStatus = Boolean(result.statusMessages?.length);
              const completedLabel = formatResultTime(result.processedAt);
              return (
                <article key={`${result.processedAt || "result"}-${resultIndex}`} className="space-y-2">
                  <div className="flex justify-end">
                    <div className="max-w-[92%] rounded-lg bg-slate-200/70 px-4 py-2.5 text-[15px] leading-6 text-slate-800 sm:max-w-[78%]">
                      <p className="whitespace-pre-wrap break-words">{resultPrompt}</p>
                    </div>
                  </div>

                  {isLatestResult ? (
                    renderSingleResult(result, resultIndex, { completedLabel })
                  ) : hasDetails ? (
                    <div>
                      {hasStatus
                        ? <ResultStatusMessages items={result.statusMessages} />
                        : <AnswerText>{resultSummary(result)}</AnswerText>}
                      <details className="group mt-2">
                        <summary className="inline-flex min-h-8 cursor-pointer list-none items-center gap-1 py-1 text-sm font-medium text-blue-600 marker:content-none">
                          View details
                          <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
                        </summary>
                        <div className="mt-2">
                          {renderSingleResult(result, resultIndex, { hideSummary: true })}
                        </div>
                      </details>
                    </div>
                  ) : (
                    hasStatus
                      ? <ResultStatusMessages items={result.statusMessages} />
                      : <AnswerText>{resultSummary(result)}</AnswerText>
                  )}
                  {!isLatestResult && completedLabel && (
                    <time className="block text-xs text-slate-400">{completedLabel}</time>
                  )}
                </article>
              );
            })}
          </div>
        )}

        {task.analysisError && !isAnalyzing && (
          <div className="sm-status sm-status-error mt-4">
            <AlertCircle className="mt-0.5 h-5 w-5 flex-shrink-0 text-red-500" />
            <div className="space-y-1">
              <p className="text-sm font-semibold text-red-900">Analysis incomplete</p>
              <p className="text-sm text-red-700">{task.analysisError}</p>
            </div>
          </div>
        )}

        {isAnalyzing && task.status === "running" && (
          <div className="mt-4">{renderAnalysisProgress()}</div>
        )}

        <div className="fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-slate-50 via-slate-50 to-transparent px-4 pb-4 pt-8 sm:px-6 lg:left-72 lg:px-8">
          <div className="mx-auto max-w-7xl">
          <div className="relative rounded-lg border border-slate-300 bg-white p-2 pr-12 shadow-md transition focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-100">
            <textarea
              ref={promptInputRef}
              value={task.prompt || ""}
              onChange={(event) => onTaskPromptChange(task.id, event.target.value)}
              onInput={(event) => resizePromptInput(event.currentTarget)}
              onKeyDown={(event) => {
                if (
                  event.key === "Enter"
                  && !event.shiftKey
                  && !event.nativeEvent.isComposing
                ) {
                  event.preventDefault();
                  if (pendingTask && isUploadReady && !isAnalyzing && task.prompt?.trim()) {
                    onAnalyzeTask(task.id);
                  }
                }
              }}
              disabled={!pendingTask || !isUploadReady || isAnalyzing}
              maxLength={MAX_ANALYSIS_QUERY_LENGTH}
              placeholder="Ask about trends, missing values, formulas, cleanup steps, or business insights..."
              aria-label="Data analysis question"
              className={`block min-h-9 max-h-24 w-full resize-none overflow-y-auto border-0 bg-transparent px-1 py-1 text-[15px] leading-6 text-slate-800 outline-none placeholder:text-slate-400 disabled:text-slate-400 ${showPromptCounter ? "pb-7" : ""}`}
              rows={1}
            />
            {showPromptCounter && (
              <span className={`absolute bottom-3 left-3 text-xs ${promptLength >= MAX_ANALYSIS_QUERY_LENGTH ? "font-medium text-red-600" : "text-amber-600"}`}>
                {promptLength}/{MAX_ANALYSIS_QUERY_LENGTH}
                {promptLength >= MAX_ANALYSIS_QUERY_LENGTH ? " · Limit reached" : ""}
              </span>
            )}
            <button
              type="button"
              aria-label={isAnalyzing && task.status === "running" ? "Analyzing" : "Analyze"}
              title={isAnalyzing && task.status === "running" ? "Analyzing" : "Analyze"}
              onClick={() => onAnalyzeTask(task.id)}
              disabled={!pendingTask || !isUploadReady || isAnalyzing || !task.prompt?.trim()}
              className="absolute bottom-2 right-2 inline-flex h-9 w-9 items-center justify-center rounded-md bg-blue-600 text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isAnalyzing && task.status === "running" ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <ArrowUp className="h-4 w-4" />
              )}
            </button>
          </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default TaskContentPanel;

import { DEFAULT_CHART_DISPLAY_LIMIT } from "../constants/chart.js";

const numericValue = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const TEMPORAL_AXIS_NAME = /(?:日期|时间|年月|月份|季度|星期|周次|date|time|month|year|quarter|week)/i;
const TEMPORAL_LABEL = /^(?:\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}\s*(?:q[1-4]|年第?[1-4]季度))/i;

export const isTemporalChartAxis = (chartData) => {
  const axisType = String(chartData?.xAxisType ?? "").toLowerCase();
  if (["datetime", "datetime-like", "date", "time", "temporal"].includes(axisType)) {
    return true;
  }
  if (["categorical", "identifier", "dimension"].includes(axisType)) {
    return false;
  }
  if (TEMPORAL_AXIS_NAME.test(String(chartData?.xAxisLabel ?? ""))) {
    return true;
  }

  const labels = chartData?.labels ?? [];
  return labels.length > 0 && labels.every((label) => TEMPORAL_LABEL.test(String(label).trim()));
};

export const buildChartDisplayData = (chartData, displayLimit) => {
  const labels = chartData?.labels ?? [];
  const series = chartData?.series ?? [];
  const totalCount = labels.length;
  const supportsTopN = !isTemporalChartAxis(chartData);
  const normalizedLimit = displayLimit === "all"
    ? "all"
    : Number(displayLimit) || DEFAULT_CHART_DISPLAY_LIMIT;

  if (!supportsTopN || normalizedLimit === "all" || totalCount <= normalizedLimit) {
    return {
      chartData,
      totalCount,
      displayedCount: totalCount,
      hiddenCount: 0,
      includesOther: false,
      supportsTopN,
      displayLimit: normalizedLimit,
    };
  }

  const rankedIndexes = labels
    .map((_, index) => index)
    .sort((left, right) => (
      numericValue(series[0]?.values?.[right])
      - numericValue(series[0]?.values?.[left])
    ));
  const visibleIndexes = rankedIndexes.slice(0, normalizedLimit);
  const hiddenIndexes = rankedIndexes.slice(normalizedLimit);
  const displayLabels = [
    ...visibleIndexes.map((index) => labels[index]),
    "Other",
  ];
  const displaySeries = series.map((item) => ({
    ...item,
    values: [
      ...visibleIndexes.map((index) => item.values?.[index] ?? null),
      hiddenIndexes.reduce(
        (total, index) => total + numericValue(item.values?.[index]),
        0
      ),
    ],
  }));

  return {
    chartData: {
      ...chartData,
      labels: displayLabels,
      series: displaySeries,
    },
    totalCount,
    displayedCount: visibleIndexes.length,
    hiddenCount: hiddenIndexes.length,
    includesOther: true,
    supportsTopN,
    displayLimit: normalizedLimit,
  };
};

export const chartDisplayMessage = (displayState) => {
  if (displayState.hiddenCount > 0) {
    return `Showing the top ${displayState.displayedCount} of ${displayState.totalCount} categories. The remaining ${displayState.hiddenCount} are combined as Other. Data and Excel include all ${displayState.totalCount}.`;
  }
  if (!displayState.supportsTopN && displayState.totalCount > 20) {
    return `The chart includes all ${displayState.totalCount} data points. Scroll horizontally to inspect every label; Data and Excel include the same complete result.`;
  }
  if (displayState.displayLimit === "all" && displayState.totalCount > 20) {
    return `Showing all ${displayState.totalCount} categories. Scroll horizontally to inspect every label; Data and Excel include the same complete result.`;
  }
  return "";
};

const DATA_RESULT_TYPES = new Set(["data", "both"]);
const CHART_RESULT_TYPES = new Set(["chart", "both"]);

export const getExpandableResultContent = (result = {}) => {
  const previews = result.previews?.length
    ? result.previews
    : result.preview
      ? [result.preview]
      : [];
  const charts = result.chartDatas?.length
    ? result.chartDatas
    : result.chartData
      ? [result.chartData]
      : [];

  return {
    hasData: DATA_RESULT_TYPES.has(result.type)
      && previews.some((preview) => preview?.columns?.length > 0),
    hasCharts: CHART_RESULT_TYPES.has(result.type)
      && charts.some((chart) => chart?.labels?.length > 0 && chart?.series?.length > 0),
    hasMetrics: Boolean(result.metrics?.length),
    hasFieldResolution: Boolean(result.fieldResolutions?.length),
    hasSheetResolution: Boolean(result.sheetResolutions?.length),
  };
};

export const hasExpandableResultContent = (result) =>
  Object.values(getExpandableResultContent(result)).some(Boolean);

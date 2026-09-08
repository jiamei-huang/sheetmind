const DEFAULT_PALETTE = ["#2563eb", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4"];


export const toAnalysisViewModel = (payload, prompt) => {
  if (payload?.type !== "result_blocks" || !Array.isArray(payload.blocks)) {
    throw new Error("后端返回了无法识别的分析结果。请刷新页面后重试。");
  }

  const table = payload.blocks.find((block) => block.kind === "table");
  const chart = payload.blocks.find((block) => block.kind === "chart");
  const summaries = payload.blocks
    .filter((block) => block.kind === "summary" && block.content)
    .map((block) => block.content);
  const metrics = payload.blocks.filter((block) => block.kind === "metric");

  const type = table && chart ? "both" : table ? "data" : chart ? "chart" : "insight_only";
  const totalRows = table?.total_rows ?? table?.rows?.length ?? 0;

  return {
    type,
    prompt,
    processedAt: new Date().toISOString(),
    suggestion: summaries.join("\n\n"),
    metrics,
    runtimeNote:
      table && table.rows.length < totalRows
        ? `前端仅展示 ${table.rows.length.toLocaleString()} 行预览，后端已处理 ${totalRows.toLocaleString()} 行。`
        : "",
    preview: table
      ? {
          columns: table.columns ?? [],
          rows: table.rows ?? [],
          totalRowCount: totalRows,
          totals: table.totals ?? {},
        }
      : null,
    chartData: chart
      ? {
          defaultType: chart.chart_type ?? "bar",
          labels: chart.labels ?? [],
          series: chart.series ?? [],
          palette: chart.palette ?? DEFAULT_PALETTE,
          xAxisLabel: chart.x_axis_label,
          yAxisLabel: chart.y_axis_label,
          confidence: chart.confidence,
          reason: chart.reason,
        }
      : null,
  };
};

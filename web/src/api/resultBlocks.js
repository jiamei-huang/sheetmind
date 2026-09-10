const DEFAULT_PALETTE = ["#2563eb", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4"];


export const toAnalysisViewModel = (payload, prompt) => {
  if (payload?.type !== "result_blocks" || !Array.isArray(payload.blocks)) {
    throw new Error("后端返回了无法识别的分析结果。请刷新页面后重试。");
  }

  const tables = payload.blocks.filter((block) => block.kind === "table");
  const table = tables[0];
  const charts = payload.blocks.filter((block) => block.kind === "chart");
  const chart = charts[0];
  const summaries = payload.blocks
    .filter((block) => block.kind === "summary" && block.content)
    .map((block) => block.content);
  const metrics = payload.blocks.filter((block) => block.kind === "metric");
  const outputIntents = Array.isArray(payload.output_intents) && payload.output_intents.length
    ? payload.output_intents
    : ["auto"];

  const type = table && chart ? "both" : table ? "data" : chart ? "chart" : "insight_only";
  const mode = chart && table
    ? "both"
    : chart || outputIntents.includes("chart")
      ? "visualization"
      : table || outputIntents.some((intent) => intent === "table" || intent === "export_excel")
        ? "processing"
        : "insight";
  const classification = outputIntents.includes("export_excel")
    ? "Excel Export"
    : mode === "visualization"
      ? "Visualization"
      : mode === "processing"
        ? "Data Processing"
        : mode === "both"
          ? "Data + Visualization"
          : "Insight";
  const totalRows = table?.total_rows ?? table?.rows?.length ?? 0;
  const previews = tables.map((tableBlock, index) => ({
    title: tableBlock.title || `结果 ${index + 1}`,
    columns: tableBlock.columns ?? [],
    rows: tableBlock.rows ?? [],
    totalRowCount: tableBlock.total_rows ?? tableBlock.rows?.length ?? 0,
    totals: tableBlock.totals ?? {},
  }));
  const chartDatas = charts.map((chartBlock, index) => ({
    title: chartBlock.title || `图表 ${index + 1}`,
    defaultType: chartBlock.chart_type ?? "bar",
    labels: chartBlock.labels ?? [],
    series: chartBlock.series ?? [],
    palette: chartBlock.palette ?? DEFAULT_PALETTE,
    xAxisLabel: chartBlock.x_axis_label,
    yAxisLabel: chartBlock.y_axis_label,
    confidence: chartBlock.confidence,
    reason: chartBlock.reason,
  }));

  return {
    type,
    outputIntents,
    mode,
    classification,
    prompt,
    processedAt: new Date().toISOString(),
    suggestion: summaries.join("\n\n"),
    metrics,
    runtimeNote:
      table && table.rows.length < totalRows
        ? `前端仅展示 ${table.rows.length.toLocaleString()} 行预览，后端已处理 ${totalRows.toLocaleString()} 行。`
        : "",
    previews,
    preview: previews[0] ?? null,
    chartDatas,
    chartData: chartDatas[0] ?? null,
  };
};

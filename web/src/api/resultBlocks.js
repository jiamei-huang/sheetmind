import { DATA_VIZ_COLORS } from "../constants/colors.js";

const DEFAULT_PALETTE = DATA_VIZ_COLORS;


export const toAnalysisViewModel = (payload, prompt) => {
  if (payload?.type !== "result_blocks" || !Array.isArray(payload.blocks)) {
    throw new Error("The backend returned an unrecognized analysis result. Refresh the page and try again.");
  }

  const tables = payload.blocks.filter((block) => block.kind === "table");
  const table = tables[0];
  const charts = payload.blocks.filter((block) => block.kind === "chart");
  const chart = charts[0];
  const summaries = payload.blocks
    .filter((block) => block.kind === "summary" && block.content)
    .map((block) => block.content);
  const metrics = payload.blocks.filter((block) => block.kind === "metric");
  const fieldResolutions = payload.blocks.filter((block) => block.kind === "field_resolution");
  const sheetResolutions = payload.blocks.filter((block) => block.kind === "sheet_resolution");
  const statusMessages = payload.blocks.filter((block) => block.kind === "status");
  const questionResults = Array.isArray(payload.questions)
    ? payload.questions.map((question) => ({
        questionId: question.question_id,
        query: question.query,
        status: question.status,
        summaries: (question.blocks ?? [])
          .filter((block) => block.kind === "summary" && block.content)
          .map((block) => block.content),
        executionReport: question.execution_report ?? null,
      }))
    : [];
  const needsFieldClarification = fieldResolutions.some(
    (block) => block.status === "needs_clarification"
  );
  const needsSheetClarification = sheetResolutions.length > 0;
  const needsClarification = needsFieldClarification || needsSheetClarification;
  const outputIntents = Array.isArray(payload.output_intents) && payload.output_intents.length
    ? payload.output_intents
    : ["auto"];

  const type = needsClarification
    ? "clarification"
    : table && chart ? "both" : table ? "data" : chart ? "chart" : "insight_only";
  const mode = needsClarification
    ? "clarification"
    : chart && table
    ? "both"
    : chart || outputIntents.includes("chart")
      ? "visualization"
      : table || outputIntents.some((intent) => intent === "table" || intent === "export_excel")
        ? "processing"
        : "insight";
  const classification = needsSheetClarification
    ? "Sheet Confirmation"
    : needsFieldClarification
    ? "Field Confirmation"
    : outputIntents.includes("export_excel")
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
    title: tableBlock.title || `Result ${index + 1}`,
    columns: tableBlock.columns ?? [],
    rows: tableBlock.rows ?? [],
    totalRowCount: tableBlock.total_rows ?? tableBlock.rows?.length ?? 0,
    totals: tableBlock.totals ?? {},
    calculationBasis: tableBlock.calculation_basis ?? null,
    artifactId: tableBlock.artifact_id ?? null,
    previewRowCount: tableBlock.preview_row_count ?? tableBlock.rows?.length ?? 0,
  }));
  const chartDatas = charts.map((chartBlock, index) => ({
    title: chartBlock.title || `Chart ${index + 1}`,
    defaultType: chartBlock.chart_type ?? "bar",
    labels: chartBlock.labels ?? [],
    series: chartBlock.series ?? [],
    palette: chartBlock.palette ?? DEFAULT_PALETTE,
    xAxisLabel: chartBlock.x_axis_label,
    xAxisType: chartBlock.x_axis_type,
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
    summaryItems: questionResults.flatMap((question) =>
      question.summaries.map((content) => ({
        questionId: question.questionId,
        query: question.query,
        status: question.status,
        content,
      }))
    ),
    status: payload.status ?? "success",
    runId: payload.run_id ?? null,
    focusQuestionId: payload.focus_question_id ?? null,
    statusMessages,
    questionResults,
    metrics,
    fieldResolutions,
    sheetResolutions,
    runtimeNote:
      table && table.rows.length < totalRows
        ? `Showing ${table.rows.length.toLocaleString()} preview rows. The backend processed ${totalRows.toLocaleString()} rows.`
        : "",
    previews,
    preview: previews[0] ?? null,
    chartDatas,
    chartData: chartDatas[0] ?? null,
  };
};

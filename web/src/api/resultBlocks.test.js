import assert from "node:assert/strict";
import test from "node:test";

import { toAnalysisViewModel } from "./resultBlocks.js";


test("converts native result blocks into the analysis view model", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      output_intents: ["table", "chart"],
      blocks: [
        { kind: "metric", label: "Revenue", value: 1200, unit: "CNY" },
        {
          kind: "table",
          title: "Sales by region",
          columns: ["region", "sales"],
          rows: [{ region: "East", sales: 1200 }],
          total_rows: 20,
          calculation_basis: {
            source_sheets: ["sales.xlsx / Sheet1"],
            fields: ["region", "sales"],
            operations: ["分组求和", "排序比较"],
            summary: "基于 sales.xlsx / Sheet1，按 region 汇总 sales。",
          },
        },
        {
          kind: "chart",
          chart_type: "bar",
          labels: ["East"],
          series: [{ name: "Sales", values: [1200] }],
          confidence: 0.91,
          reason: "categorical X-axis + numeric metric",
        },
        { kind: "summary", content: "East leads sales." },
      ],
    },
    "Sales by region"
  );

  assert.equal(result.type, "both");
  assert.deepEqual(result.outputIntents, ["table", "chart"]);
  assert.equal(result.mode, "both");
  assert.equal(result.classification, "Data + Visualization");
  assert.equal(result.metrics[0].value, 1200);
  assert.equal(result.preview.totalRowCount, 20);
  assert.equal(result.preview.title, "Sales by region");
  assert.deepEqual(result.preview.calculationBasis.fields, ["region", "sales"]);
  assert.equal(result.chartData.defaultType, "bar");
  assert.equal(result.chartData.confidence, 0.91);
  assert.match(result.chartData.reason, /numeric metric/);
  assert.equal(result.suggestion, "East leads sales.");
  assert.match(result.runtimeNote, /1.*20/);
});


test("uses backend output intent for Excel export classification", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      output_intents: ["export_excel"],
      blocks: [
        { kind: "table", columns: ["sku"], rows: [{ sku: "A" }] },
      ],
    },
    "生成 Excel"
  );

  assert.equal(result.mode, "processing");
  assert.equal(result.classification, "Excel Export");
});


test("rejects obsolete response shapes", () => {
  assert.throws(
    () => toAnalysisViewModel({ type: "data", data: {} }, "query"),
    /无法识别/
  );
});


test("keeps every table block as a named preview", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      blocks: [
        {
          kind: "table",
          title: "筛选2025年数据",
          columns: ["year", "amount"],
          rows: [{ year: 2025, amount: 100 }],
        },
        {
          kind: "table",
          title: "按金额降序排序",
          columns: ["year", "amount"],
          rows: [{ year: 2024, amount: 999 }],
        },
      ],
    },
    "两个独立问题"
  );

  assert.equal(result.previews.length, 2);
  assert.equal(result.previews[0].title, "筛选2025年数据");
  assert.equal(result.previews[1].rows[0].amount, 999);
  assert.deepEqual(result.preview, result.previews[0]);
});


test("keeps every chart block as a named visualization", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      blocks: [
        { kind: "chart", title: "地区销售额", chart_type: "bar", labels: ["华东"], series: [{ name: "销售额", values: [10] }] },
        { kind: "chart", title: "月度趋势", chart_type: "line", labels: ["1月"], series: [{ name: "销售额", values: [20] }] },
      ],
    },
    "生成两个图"
  );

  assert.equal(result.chartDatas.length, 2);
  assert.equal(result.chartDatas[1].title, "月度趋势");
  assert.deepEqual(result.chartData, result.chartDatas[0]);
});

test("keeps field clarification choices in the analysis view model", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      blocks: [
        {
          kind: "field_resolution",
          status: "needs_clarification",
          reference: "金额",
          message: "“金额”可能对应多个字段，请选择。",
          candidates: [
            { column: "金额", confidence: 0.5, reason: "名称匹配" },
            { column: "金额（RMB）", confidence: 0.5, reason: "名称匹配" },
          ],
        },
      ],
    },
    "汇总金额"
  );

  assert.equal(result.type, "clarification");
  assert.equal(result.mode, "clarification");
  assert.equal(result.classification, "Field Confirmation");
  assert.equal(result.fieldResolutions[0].reference, "金额");
  assert.equal(result.fieldResolutions[0].candidates[1].column, "金额（RMB）");
});

test("keeps sheet conflict choices in the analysis view model", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      blocks: [
        {
          kind: "sheet_resolution",
          status: "scope_conflict",
          message: "Sheet2 更匹配当前问题。",
          current_sheets: ["Sheet1"],
          candidates: [
            {
              candidate_id: "sales.xlsx::Sheet2",
              file_name: "sales.xlsx",
              sheet_name: "Sheet2",
              columns: ["店铺", "金额（RMB）"],
              confidence: 0.91,
              reason: "column match",
            },
          ],
        },
      ],
    },
    "汇总人民币金额"
  );

  assert.equal(result.type, "clarification");
  assert.equal(result.mode, "clarification");
  assert.equal(result.classification, "Sheet Confirmation");
  assert.equal(result.sheetResolutions[0].candidates[0].sheet_name, "Sheet2");
});

test("keeps one conclusion per question and full-result artifact ids", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
      run_id: "run-1",
      status: "success",
      focus_question_id: "q2",
      output_intents: ["table"],
      questions: [
        {
          question_id: "q1",
          query: "哪个物流商最高",
          status: "success",
          blocks: [{ kind: "summary", content: "日本海外仓最高。" }],
        },
        {
          question_id: "q2",
          query: "哪个平台最高",
          status: "success",
          blocks: [{ kind: "summary", content: "亚马逊最高。" }],
        },
      ],
      blocks: [
        { kind: "summary", content: "日本海外仓最高。" },
        {
          kind: "table",
          columns: ["物流商", "费用"],
          rows: [{ 物流商: "日本海外仓", 费用: 300 }],
          total_rows: 1205,
          preview_row_count: 1000,
          artifact_id: "artifact-1",
        },
        { kind: "summary", content: "亚马逊最高。" },
      ],
    },
    "两个问题"
  );

  assert.deepEqual(result.summaryItems.map((item) => item.content), [
    "日本海外仓最高。", "亚马逊最高。",
  ]);
  assert.equal(result.preview.artifactId, "artifact-1");
  assert.equal(result.runId, "run-1");
  assert.equal(result.focusQuestionId, "q2");
});

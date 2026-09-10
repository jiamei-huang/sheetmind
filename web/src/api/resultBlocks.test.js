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
          columns: ["region", "sales"],
          rows: [{ region: "East", sales: 1200 }],
          total_rows: 20,
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

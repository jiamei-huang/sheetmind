import assert from "node:assert/strict";
import test from "node:test";

import { toAnalysisViewModel } from "./resultBlocks.js";


test("converts native result blocks into the analysis view model", () => {
  const result = toAnalysisViewModel(
    {
      type: "result_blocks",
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
        },
        { kind: "summary", content: "East leads sales." },
      ],
    },
    "Sales by region"
  );

  assert.equal(result.type, "both");
  assert.equal(result.metrics[0].value, 1200);
  assert.equal(result.preview.totalRowCount, 20);
  assert.equal(result.chartData.defaultType, "bar");
  assert.equal(result.suggestion, "East leads sales.");
  assert.match(result.runtimeNote, /1.*20/);
});


test("rejects obsolete response shapes", () => {
  assert.throws(
    () => toAnalysisViewModel({ type: "data", data: {} }, "query"),
    /无法识别/
  );
});

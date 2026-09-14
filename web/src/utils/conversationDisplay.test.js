import test from "node:test";
import assert from "node:assert/strict";

import { hasExpandableResultContent } from "./conversationDisplay.js";

test("pure conclusions and failures do not render an empty details disclosure", () => {
  assert.equal(hasExpandableResultContent({ type: "insight_only", suggestion: "结论" }), false);
  assert.equal(
    hasExpandableResultContent({
      type: "insight_only",
      statusMessages: [{ status: "failed", message: "查询失败" }],
    }),
    false
  );
});

test("renderable evidence keeps the historical details disclosure", () => {
  assert.equal(
    hasExpandableResultContent({
      type: "data",
      preview: { columns: ["平台", "费用"], rows: [["乐天", 120]] },
    }),
    true
  );
  assert.equal(
    hasExpandableResultContent({
      type: "chart",
      chartData: { labels: ["乐天"], series: [{ name: "费用", values: [120] }] },
    }),
    true
  );
});

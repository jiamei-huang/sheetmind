import assert from "node:assert/strict";
import test from "node:test";

import { conversationHistoryToResults } from "./conversations.js";


test("restores legacy text-only conversation pairs after refresh", () => {
  const results = conversationHistoryToResults([
    { role: "user", content: "哪个店铺最高", createdAt: "2026-09-11T10:00:00" },
    { role: "assistant", content: "乐天最高", createdAt: "2026-09-11T10:00:01" },
  ]);

  assert.equal(results.length, 1);
  assert.equal(results[0].prompt, "哪个店铺最高");
  assert.equal(results[0].suggestion, "乐天最高");
  assert.equal(results[0].type, "insight_only");
});


test("restores native result blocks when conversation metadata is available", () => {
  const results = conversationHistoryToResults([
    { role: "user", content: "哪个平台最高", createdAt: "2026-09-11T10:00:00" },
    {
      role: "assistant",
      content: "美国官网最高",
      createdAt: "2026-09-11T10:00:01",
      metadata: {
        result: {
          type: "result_blocks",
          output_intents: ["table"],
          blocks: [
            { kind: "summary", content: "美国官网最高" },
            {
              kind: "table",
              columns: ["平台", "费用金额"],
              rows: [{ 平台: "美国官网", 费用金额: 35000 }],
              total_rows: 1,
              totals: { 费用金额: 35000 },
              calculation_basis: {
                source_sheets: ["费用.xlsx / 尾程"],
                fields: ["平台", "费用金额"],
                operations: ["分组求和", "排序比较"],
                summary: "基于尾程 Sheet 按平台汇总费用金额。",
              },
            },
          ],
        },
      },
    },
  ]);

  assert.equal(results.length, 1);
  assert.equal(results[0].prompt, "哪个平台最高");
  assert.equal(results[0].preview.rows[0].平台, "美国官网");
  assert.equal(results[0].preview.calculationBasis.source_sheets[0], "费用.xlsx / 尾程");
  assert.equal(results[0].previews.length, 1);
  assert.equal(results[0].processedAt, "2026-09-11T10:00:01");
});

test("restores an interrupted run as a failure instead of an insight", () => {
  const results = conversationHistoryToResults([
    { role: "user", content: "哪个平台最高", metadata: { runId: "run-2" } },
    {
      role: "assistant",
      content: "分析执行中断，请重新提交该问题。",
      metadata: { runId: "run-2", status: "failed" },
    },
  ]);

  assert.equal(results[0].status, "failed");
  assert.equal(results[0].suggestion, "");
  assert.equal(results[0].statusMessages[0].status, "failed");
});

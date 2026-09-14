import test from "node:test";
import assert from "node:assert/strict";

import { normalizeAnalysisMarkdown } from "./analysisMarkdown.js";

test("legacy inline insight text becomes a heading and readable list", () => {
  const result = normalizeAnalysisMarkdown(
    "**Insight：** 🟢 最高值：A。 🔴 最低值：B。 📊 分析：分布不均。"
  );

  assert.equal(
    result,
    "### Insight：\n\n- 🟢 最高值：A。\n\n- 🔴 最低值：B。\n\n- 📊 分析：分布不均。"
  );
});

test("normal markdown paragraphs are preserved without truncation", () => {
  const content = `### 结论\n\n${"完整回答".repeat(100)}`;

  assert.equal(normalizeAnalysisMarkdown(content), content);
});

test("plain Chinese section labels become headings", () => {
  const result = normalizeAnalysisMarkdown(
    "第一段说明。\n\n关键发现：第一项。\n\n业务建议：检查数据。"
  );

  assert.equal(
    result,
    "第一段说明。\n\n### 关键发现\n\n第一项。\n\n### 业务建议\n\n检查数据。"
  );
});

test("Chinese numbered findings become separate list items", () => {
  const result = normalizeAnalysisMarkdown(
    "业务建议：一是检查数据；二是补充样本；三是持续监控。"
  );

  assert.equal(
    result,
    "### 业务建议\n\n- 一是检查数据\n- 二是补充样本\n- 三是持续监控。"
  );
});

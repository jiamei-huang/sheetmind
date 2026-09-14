import assert from "node:assert/strict";
import test from "node:test";

import {
  CATEGORY_COLORS,
  categoryColor,
  pieVisibilityStats,
  toggleHiddenItem,
} from "./chartLegend.js";
import { DATA_VIZ_OTHER } from "../constants/colors.js";

test("category colors stay stable across chart types", () => {
  assert.equal(categoryColor("A", 0), CATEGORY_COLORS[0]);
  assert.equal(categoryColor("B", 1), CATEGORY_COLORS[1]);
  assert.equal(categoryColor("Other", 10), DATA_VIZ_OTHER);
  assert.equal(categoryColor("其他", 3), DATA_VIZ_OTHER);
});

test("legend items can be hidden and restored", () => {
  const hidden = toggleHiddenItem(new Set(), "Other", ["A", "B", "Other"]);
  assert.deepEqual([...hidden], ["Other"]);
  assert.equal(toggleHiddenItem(hidden, "Other", ["A", "B", "Other"]).size, 0);
});

test("multiple legend items can be hidden while the remainder stays visible", () => {
  const keys = ["A", "B", "C", "Other"];
  const first = toggleHiddenItem(new Set(), "A", keys);
  const second = toggleHiddenItem(first, "Other", keys);

  assert.deepEqual([...second].sort(), ["A", "Other"]);
  assert.equal(second.has("B"), false);
  assert.equal(second.has("C"), false);
});

test("the final visible legend item cannot be hidden", () => {
  const hidden = new Set(["A", "B"]);
  const next = toggleHiddenItem(hidden, "Other", ["A", "B", "Other"]);
  assert.deepEqual([...next].sort(), ["A", "B"]);
});

test("pie visibility reports the hidden share of the complete total", () => {
  const stats = pieVisibilityStats(
    [{ name: "A", value: 60 }, { name: "Other", value: 40 }],
    new Set(["Other"])
  );

  assert.equal(stats.fullTotal, 100);
  assert.equal(stats.visibleTotal, 60);
  assert.equal(stats.hiddenPercent, 40);
});

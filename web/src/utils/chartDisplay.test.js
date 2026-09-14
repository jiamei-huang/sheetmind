import assert from "node:assert/strict";
import test from "node:test";

import {
  buildChartDisplayData,
  chartDisplayMessage,
  isTemporalChartAxis,
} from "./chartDisplay.js";

const categoricalChart = {
  defaultType: "bar",
  labels: ["A", "B", "C", "D"],
  series: [
    { name: "费用金额", values: [10, 40, 30, 20] },
    { name: "订单数", values: [1, 4, 3, 2] },
  ],
};

test("Top N affects only display data and combines the remainder", () => {
  const state = buildChartDisplayData(categoricalChart, 2);

  assert.deepEqual(state.chartData.labels, ["B", "C", "Other"]);
  assert.deepEqual(state.chartData.series[0].values, [40, 30, 30]);
  assert.deepEqual(state.chartData.series[1].values, [4, 3, 3]);
  assert.equal(state.totalCount, 4);
  assert.equal(state.hiddenCount, 2);
  assert.deepEqual(categoricalChart.labels, ["A", "B", "C", "D"]);
  assert.match(chartDisplayMessage(state), /Data and Excel include all 4/);
});

test("All preserves every category without an Other bucket", () => {
  const state = buildChartDisplayData(categoricalChart, "all");

  assert.equal(state.chartData, categoricalChart);
  assert.equal(state.displayedCount, 4);
  assert.equal(state.hiddenCount, 0);
  assert.equal(state.includesOther, false);
});

test("temporal line charts preserve their complete sequence", () => {
  const lineChart = {
    ...categoricalChart,
    defaultType: "line",
    xAxisType: "datetime",
  };

  const state = buildChartDisplayData(lineChart, 2);

  assert.equal(state.chartData, lineChart);
  assert.equal(state.supportsTopN, false);
  assert.deepEqual(state.chartData.labels, ["A", "B", "C", "D"]);
});

test("large line charts explain that all points are still present", () => {
  const lineChart = {
    defaultType: "line",
    xAxisLabel: "日期",
    labels: Array.from({ length: 30 }, (_, index) => `Day ${index + 1}`),
    series: [{
      name: "费用金额",
      values: Array.from({ length: 30 }, (_, index) => index),
    }],
  };

  const state = buildChartDisplayData(lineChart, 10);

  assert.match(chartDisplayMessage(state), /includes all 30 data points/);
});

test("a historical categorical line result still supports Top N", () => {
  const historicalChart = {
    defaultType: "line",
    labels: ["A", "B", "C", "D"],
    series: [{ name: "费用金额", values: [10, 40, 30, 20] }],
  };

  const state = buildChartDisplayData(historicalChart, 2);

  assert.equal(state.supportsTopN, true);
  assert.deepEqual(state.chartData.labels, ["B", "C", "Other"]);
  assert.deepEqual(state.chartData.series[0].values, [40, 30, 30]);
});

test("legacy date labels are recognized as temporal without axis metadata", () => {
  const chart = {
    labels: ["2026-01-01", "2026-02-01"],
    series: [{ name: "金额", values: [10, 20] }],
  };

  assert.equal(isTemporalChartAxis(chart), true);
});

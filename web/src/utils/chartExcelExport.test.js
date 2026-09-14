import assert from "node:assert/strict";
import test from "node:test";

import {
  chartDataToRows,
  createChartWorkbookBuffer,
  createTableWorkbookBuffer,
  sanitizeWorksheetName,
  tableDataToRows,
} from "./chartExcelExport.js";

const chartData = {
  title: "乐天/SKU:费用趋势图",
  xAxisLabel: "易仓SKU",
  labels: ["00123", "ZN0140B"],
  series: [{ name: "费用金额", values: [1280.5, 980] }],
};

test("chartDataToRows keeps labels as text and metrics as numbers", () => {
  const rows = chartDataToRows(chartData);

  assert.deepEqual(rows, [
    ["易仓SKU", "费用金额"],
    ["00123", 1280.5],
    ["ZN0140B", 980],
  ]);
});

test("sanitizeWorksheetName produces a valid Excel worksheet name", () => {
  assert.equal(sanitizeWorksheetName(chartData.title), "乐天 SKU 费用趋势图");
  assert.equal(sanitizeWorksheetName("x".repeat(40)), "x".repeat(31));
});

test("createChartWorkbookBuffer writes a readable xlsx workbook", async () => {
  const buffer = await createChartWorkbookBuffer(chartData);
  const excelJsModule = await import("exceljs");
  const Workbook = excelJsModule.Workbook ?? excelJsModule.default?.Workbook;
  const workbook = new Workbook();
  await workbook.xlsx.load(buffer);

  const worksheet = workbook.worksheets[0];
  assert.equal(worksheet.getCell("A1").value, "易仓SKU");
  assert.equal(worksheet.getCell("A2").value, "00123");
  assert.equal(worksheet.getCell("A2").numFmt, "@");
  assert.equal(worksheet.getCell("B2").value, 1280.5);
});

test("tableDataToRows preserves source column order", () => {
  const rows = tableDataToRows({
    columns: ["易仓SKU", "费用金额"],
    rows: [
      { 费用金额: 1280.5, 易仓SKU: "00123" },
      { 易仓SKU: "ZN0140B", 费用金额: 980 },
    ],
  });

  assert.deepEqual(rows, [
    ["易仓SKU", "费用金额"],
    ["00123", 1280.5],
    ["ZN0140B", 980],
  ]);
});

test("createTableWorkbookBuffer writes a readable evidence workbook", async () => {
  const buffer = await createTableWorkbookBuffer({
    title: "尾程/SKU费用",
    columns: ["易仓SKU", "费用金额"],
    rows: [{ 易仓SKU: "00123", 费用金额: 1280.5 }],
  });
  const excelJsModule = await import("exceljs");
  const Workbook = excelJsModule.Workbook ?? excelJsModule.default?.Workbook;
  const workbook = new Workbook();
  await workbook.xlsx.load(buffer);

  assert.equal(workbook.worksheets[0].name, "尾程 SKU费用");
  assert.equal(workbook.worksheets[0].getCell("A2").value, "00123");
  assert.equal(workbook.worksheets[0].getCell("B2").value, 1280.5);
});

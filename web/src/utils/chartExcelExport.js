const INVALID_SHEET_NAME_CHARS = /[\\/*?:[\]]/g;

export const chartDataToRows = (chartData) => {
  const labels = chartData?.labels ?? [];
  const series = chartData?.series ?? [];
  const xAxisHeader = chartData?.xAxisLabel || "Label";

  return [
    [xAxisHeader, ...series.map((item) => item.name || "Value")],
    ...labels.map((label, index) => [
      String(label ?? ""),
      ...series.map((item) => item.values?.[index] ?? null),
    ]),
  ];
};

export const tableDataToRows = (preview) => {
  const columns = preview?.columns ?? [];
  const rows = preview?.rows ?? [];

  return [
    columns,
    ...rows.map((row) => columns.map((column) => row?.[column] ?? null)),
  ];
};

export const sanitizeWorksheetName = (value) => {
  const safeName = String(value || "Chart Data")
    .replace(INVALID_SHEET_NAME_CHARS, " ")
    .trim();
  return (safeName || "Chart Data").slice(0, 31);
};

const createWorkbookBuffer = async (rows, title) => {
  const excelJsModule = await import("exceljs");
  const Workbook = excelJsModule.Workbook ?? excelJsModule.default?.Workbook;
  const workbook = new Workbook();
  workbook.creator = "SheetMind";
  workbook.created = new Date();

  const worksheet = workbook.addWorksheet(
    sanitizeWorksheetName(title)
  );
  rows.forEach((row) => worksheet.addRow(row));

  const header = worksheet.getRow(1);
  header.font = { bold: true, color: { argb: "FF172033" } };
  header.fill = {
    type: "pattern",
    pattern: "solid",
    fgColor: { argb: "FFEFF4FA" },
  };
  header.alignment = { vertical: "middle" };
  header.height = 22;
  worksheet.views = [{ state: "frozen", ySplit: 1 }];
  worksheet.autoFilter = {
    from: { row: 1, column: 1 },
    to: { row: 1, column: Math.max(rows[0]?.length ?? 1, 1) },
  };

  worksheet.columns.forEach((column, columnIndex) => {
    const values = rows.map((row) => String(row[columnIndex] ?? ""));
    column.width = Math.min(
      40,
      Math.max(12, ...values.map((value) => value.length + 2))
    );
  });
  worksheet.getColumn(1).numFmt = "@";

  return workbook.xlsx.writeBuffer();
};

export const createChartWorkbookBuffer = async (chartData) =>
  createWorkbookBuffer(chartDataToRows(chartData), chartData?.title);

export const createTableWorkbookBuffer = async (preview) =>
  createWorkbookBuffer(tableDataToRows(preview), preview?.title || "Analysis Data");

const downloadWorkbook = (buffer, fileName) => {
  const blob = new Blob(
    [buffer],
    { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${fileName}.xlsx`;
  link.click();
  URL.revokeObjectURL(url);
};

export const downloadChartDataAsExcel = async (chartData, fileName) => {
  const buffer = await createChartWorkbookBuffer(chartData);
  downloadWorkbook(buffer, fileName);
};

export const downloadTableDataAsExcel = async (preview, fileName) => {
  const buffer = await createTableWorkbookBuffer(preview);
  downloadWorkbook(buffer, fileName);
};

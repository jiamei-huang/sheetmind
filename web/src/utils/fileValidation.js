export const SUPPORTED_EXCEL_EXTENSIONS = [".xlsx", ".xls"];

export const unsupportedUploadNames = (files = []) =>
  files
    .filter((file) => {
      const name = String(file?.name || "").toLowerCase();
      return !SUPPORTED_EXCEL_EXTENSIONS.some((extension) => name.endsWith(extension));
    })
    .map((file) => String(file?.name || "Unnamed file"));

export const unsupportedUploadMessage = (fileNames = []) =>
  `不支持以下文件：${fileNames.join("、")}。当前仅支持 Excel 文件（.xlsx、.xls）。`;

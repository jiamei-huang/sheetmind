export const SUPPORTED_EXCEL_EXTENSIONS = [".xlsx", ".xls"];
export const MAX_EXCEL_FILE_SIZE_MB = 25;
export const MAX_EXCEL_FILE_SIZE_BYTES = MAX_EXCEL_FILE_SIZE_MB * 1024 * 1024;

export const unsupportedUploadNames = (files = []) =>
  files
    .filter((file) => {
      const name = String(file?.name || "").toLowerCase();
      return !SUPPORTED_EXCEL_EXTENSIONS.some((extension) => name.endsWith(extension));
    })
    .map((file) => String(file?.name || "Unnamed file"));

export const unsupportedUploadMessage = (fileNames = []) =>
  `Unsupported files: ${fileNames.join(", ")}. SheetMind currently supports Excel files only (.xlsx, .xls).`;

export const oversizedUploadNames = (files = []) =>
  files
    .filter((file) => Number(file?.size || 0) > MAX_EXCEL_FILE_SIZE_BYTES)
    .map((file) => String(file?.name || "Unnamed file"));

export const oversizedUploadMessage = (fileNames = []) =>
  `Files too large: ${fileNames.join(", ")}. Each Excel file must be ${MAX_EXCEL_FILE_SIZE_MB} MB or smaller.`;

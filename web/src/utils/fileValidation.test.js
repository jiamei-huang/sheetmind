import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_EXCEL_FILE_SIZE_BYTES,
  oversizedUploadNames,
  unsupportedUploadNames,
} from "./fileValidation.js";

test("accepts supported Excel extensions regardless of case", () => {
  assert.deepEqual(unsupportedUploadNames([
    { name: "sales.xlsx" },
    { name: "legacy.xls" },
  ]), []);
});

test("rejects XLSM, CSV, and unrelated files before upload", () => {
  assert.deepEqual(unsupportedUploadNames([
    { name: "model.xlsm" },
    { name: "sales.csv" },
    { name: "notes.rtf" },
    { name: "image.png" },
  ]), ["model.xlsm", "sales.csv", "notes.rtf", "image.png"]);
});

test("rejects Excel files above the upload size limit", () => {
  assert.deepEqual(oversizedUploadNames([
    { name: "small.xlsx", size: MAX_EXCEL_FILE_SIZE_BYTES },
    { name: "large.xlsx", size: MAX_EXCEL_FILE_SIZE_BYTES + 1 },
  ]), ["large.xlsx"]);
});

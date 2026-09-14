import assert from "node:assert/strict";
import test from "node:test";

import { unsupportedUploadNames } from "./fileValidation.js";

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

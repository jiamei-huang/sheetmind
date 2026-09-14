import assert from "node:assert/strict";
import test from "node:test";

import { clearAnonymousBrowserState } from "./session.js";


const createStorage = (entries) => {
  const values = new Map(entries);
  return {
    get length() {
      return values.size;
    },
    key(index) {
      return Array.from(values.keys())[index] ?? null;
    },
    removeItem(key) {
      values.delete(key);
    },
    has(key) {
      return values.has(key);
    },
  };
};


test("clearAnonymousBrowserState removes only SheetMind workspace keys", () => {
  const storage = createStorage([
    ["sheetmind_active_project", "project-1"],
    ["excel_project-1", "[]"],
    ["tasks_project-1", "[]"],
    ["lastActiveTask_project-1", "task-1"],
    ["unrelated_preference", "keep"],
  ]);

  clearAnonymousBrowserState(storage);

  assert.equal(storage.has("sheetmind_active_project"), false);
  assert.equal(storage.has("excel_project-1"), false);
  assert.equal(storage.has("tasks_project-1"), false);
  assert.equal(storage.has("lastActiveTask_project-1"), false);
  assert.equal(storage.has("unrelated_preference"), true);
});

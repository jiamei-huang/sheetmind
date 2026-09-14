import assert from "node:assert/strict";
import test from "node:test";

import {
  analyzeStream,
  buildSelectedFileScope,
  toAnalysisErrorMessage,
} from "./analysis.js";

test("buildSelectedFileScope sends persisted file names and every selected sheet", () => {
  assert.deepEqual(buildSelectedFileScope([
    {
      fileId: "file-tail",
      name: "12月尾程仓储汇总.xlsx",
      selectedSheets: ["尾程", "仓储"],
    },
    {
      fileId: "file-map",
      fileName: "平台映射.xlsx",
      selectedSheets: ["平台匹配"],
    },
    {
      name: "未选择.xlsx",
      selectedSheets: [],
    },
  ]), [
    { fileId: "file-tail", fileName: "12月尾程仓储汇总.xlsx", sheets: ["尾程", "仓储"] },
    { fileId: "file-map", fileName: "平台映射.xlsx", sheets: ["平台匹配"] },
  ]);
});

test("buildSelectedFileScope keeps same-name workbook versions separate", () => {
  assert.deepEqual(buildSelectedFileScope([
    { fileId: "version-1", name: "汇总.xlsx", selectedSheets: ["尾程"] },
    { fileId: "version-2", name: "汇总.xlsx", selectedSheets: ["尾程"] },
  ]), [
    { fileId: "version-1", fileName: "汇总.xlsx", sheets: ["尾程"] },
    { fileId: "version-2", fileName: "汇总.xlsx", sheets: ["尾程"] },
  ]);
});

test("analyzeStream includes anonymous session cookies for cross-origin streaming", async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  let fetchOptions;

  globalThis.window = {
    setTimeout,
    clearTimeout,
  };
  globalThis.fetch = async (_url, options) => {
    fetchOptions = options;
    return {
      ok: true,
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(
              'data: {"event":"done","result":{"type":"result_blocks","blocks":[]}}\n\n'
            )
          );
          controller.close();
        },
      }),
    };
  };

  try {
    await analyzeStream({
      taskId: "task-1",
      query: "summarize",
      selectedFiles: [],
    });
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
  }

  assert.equal(fetchOptions.credentials, "include");
});

test("keeps the backend's localized API quota message", () => {
  assert.equal(toAnalysisErrorMessage({
    response: { data: { detail: "API 额度已耗尽，请稍后再试。" } },
  }), "API 额度已耗尽，请稍后再试。");
});

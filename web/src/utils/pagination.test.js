import assert from "node:assert/strict";
import test from "node:test";

import { buildPaginationState } from "./pagination.js";

test("page size remains available when a larger size reduces the result to one page", () => {
  const state = buildPaginationState(25, 50, 1);

  assert.equal(state.totalPages, 1);
  assert.equal(state.showPageSize, true);
  assert.equal(state.showNavigation, false);
});

test("page navigation appears only when the result spans multiple pages", () => {
  const state = buildPaginationState(80, 50, 3);

  assert.equal(state.totalPages, 2);
  assert.equal(state.currentPage, 2);
  assert.equal(state.showPageSize, true);
  assert.equal(state.showNavigation, true);
});

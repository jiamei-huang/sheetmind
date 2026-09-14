import assert from "node:assert/strict";
import test from "node:test";

import { buildYAxisScale } from "./chartAxis.js";

test("comparison scales start at zero and keep every tick inside the domain", () => {
  const scale = buildYAxisScale([124, 304238], { includeZero: true });

  assert.equal(scale.min, 0);
  assert.ok(scale.max >= 304238);
  assert.equal(scale.ticks[0], scale.min);
  assert.equal(scale.ticks.at(-1), scale.max);
});

test("focused trend scales can omit zero without rendering out-of-domain ticks", () => {
  const scale = buildYAxisScale([1000, 1100, 1200]);

  assert.ok(scale.min > 0);
  assert.ok(scale.min <= 1000);
  assert.ok(scale.max >= 1200);
  assert.ok(scale.ticks.every((tick) => tick >= scale.min && tick <= scale.max));
});

test("negative comparison scales still include zero", () => {
  const scale = buildYAxisScale([-50, -10], { includeZero: true });

  assert.equal(scale.max, 0);
  assert.ok(scale.min <= -50);
});

test("empty scales use a stable default", () => {
  assert.deepEqual(buildYAxisScale([]), {
    min: 0,
    max: 100,
    ticks: [0, 25, 50, 75, 100],
  });
});

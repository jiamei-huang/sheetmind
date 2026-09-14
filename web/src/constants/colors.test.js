import assert from "node:assert/strict";
import test from "node:test";

import {
  BRAND_PRIMARY,
  DATA_VIZ_COLORS,
  ERROR_SCALE,
  SUCCESS_SCALE,
  WARNING_SCALE,
} from "./colors.js";

const relativeLuminance = (hex) => {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)
    .map((part) => Number.parseInt(part, 16) / 255)
    .map((value) => (
      value <= 0.04045
        ? value / 12.92
        : ((value + 0.055) / 1.055) ** 2.4
    ));
  return (0.2126 * channels[0]) + (0.7152 * channels[1]) + (0.0722 * channels[2]);
};

const contrastAgainstWhite = (hex) => 1.05 / (relativeLuminance(hex) + 0.05);

test("brand and semantic action colors support white text", () => {
  [BRAND_PRIMARY, SUCCESS_SCALE[600], WARNING_SCALE[600], ERROR_SCALE[600]]
    .forEach((color) => assert.ok(contrastAgainstWhite(color) >= 4.5, color));
});

test("categorical chart colors are distinct and support white data labels", () => {
  assert.equal(new Set(DATA_VIZ_COLORS).size, DATA_VIZ_COLORS.length);
  DATA_VIZ_COLORS.forEach(
    (color) => assert.ok(contrastAgainstWhite(color) >= 4.5, color)
  );
});

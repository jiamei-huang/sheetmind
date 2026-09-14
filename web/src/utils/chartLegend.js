import { DATA_VIZ_COLORS, DATA_VIZ_OTHER } from "../constants/colors.js";

const numericValue = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

export const CATEGORY_COLORS = DATA_VIZ_COLORS;

export const categoryColor = (label, index) => {
  const normalizedLabel = String(label ?? "").trim().toLowerCase();
  if (normalizedLabel === "other" || normalizedLabel === "其他") {
    return DATA_VIZ_OTHER;
  }
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length];
};

export const toggleHiddenItem = (hiddenItems, itemKey, allKeys) => {
  const next = new Set(hiddenItems ?? []);
  if (next.has(itemKey)) {
    next.delete(itemKey);
    return next;
  }

  const visibleCount = allKeys.filter((key) => !next.has(key)).length;
  if (visibleCount > 1) {
    next.add(itemKey);
  }
  return next;
};

export const pieVisibilityStats = (items, hiddenItems) => {
  const hidden = hiddenItems instanceof Set ? hiddenItems : new Set(hiddenItems ?? []);
  const fullTotal = items.reduce((total, item) => total + numericValue(item.value), 0);
  const hiddenTotal = items.reduce(
    (total, item) => total + (hidden.has(item.name) ? numericValue(item.value) : 0),
    0
  );
  const visibleTotal = fullTotal - hiddenTotal;

  return {
    fullTotal,
    hiddenTotal,
    visibleTotal,
    hiddenCount: hidden.size,
    hiddenPercent: fullTotal > 0 ? (hiddenTotal / fullTotal) * 100 : 0,
  };
};

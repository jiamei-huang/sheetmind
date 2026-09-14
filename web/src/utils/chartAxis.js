const finiteNumbers = (values) => (values ?? [])
  .map((value) => Number(value))
  .filter(Number.isFinite);

const niceNumber = (value, round) => {
  if (!Number.isFinite(value) || value <= 0) return 1;

  const exponent = Math.floor(Math.log10(value));
  const fraction = value / (10 ** exponent);
  let niceFraction;

  if (round) {
    if (fraction < 1.5) niceFraction = 1;
    else if (fraction < 3) niceFraction = 2;
    else if (fraction < 7) niceFraction = 5;
    else niceFraction = 10;
  } else if (fraction <= 1) niceFraction = 1;
  else if (fraction <= 2) niceFraction = 2;
  else if (fraction <= 5) niceFraction = 5;
  else niceFraction = 10;

  return niceFraction * (10 ** exponent);
};

const roundedTick = (value, step) => {
  const decimals = Math.max(0, -Math.floor(Math.log10(Math.abs(step))) + 1);
  return Number(value.toFixed(Math.min(decimals, 10)));
};

export const buildYAxisScale = (
  values,
  { includeZero = false, targetTicks = 5, paddingRatio = 0.1 } = {}
) => {
  const numbers = finiteNumbers(values);
  if (numbers.length === 0) {
    return { min: 0, max: 100, ticks: [0, 25, 50, 75, 100] };
  }

  const actualMin = Math.min(...numbers);
  const actualMax = Math.max(...numbers);
  let min = includeZero ? Math.min(0, actualMin) : actualMin;
  let max = includeZero ? Math.max(0, actualMax) : actualMax;
  const dataRange = max - min;

  if (dataRange === 0) {
    const padding = Math.max(Math.abs(max) * paddingRatio, 1);
    if (includeZero && max > 0) {
      min = 0;
      max += padding;
    } else if (includeZero && min < 0) {
      min -= padding;
      max = 0;
    } else {
      min -= padding;
      max += padding;
    }
  } else if (includeZero) {
    if (min < 0) min -= dataRange * paddingRatio;
    if (max > 0) max += dataRange * paddingRatio;
  } else {
    min -= dataRange * paddingRatio;
    max += dataRange * paddingRatio;
    if (actualMin >= 0) min = Math.max(0, min);
  }

  const range = Math.max(max - min, 1);
  const step = niceNumber(range / Math.max(targetTicks - 1, 1), true);
  const niceMin = roundedTick(Math.floor(min / step) * step, step);
  const niceMax = roundedTick(Math.ceil(max / step) * step, step);
  const ticks = [];

  for (let value = niceMin; value <= niceMax + step * 0.01; value += step) {
    ticks.push(roundedTick(value, step));
  }

  return { min: niceMin, max: niceMax, ticks };
};

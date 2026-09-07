import React, { useState, useMemo, useEffect, useRef } from "react";
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

// 主色：与左侧 New Task 按钮一致（Tailwind blue-600）
const PRIMARY_BLUE = "#2563eb";

// 现代 SaaS 风格的柔和渐变色板（主色统一为 New Task 蓝）
const GRADIENT_PALETTE = [
  { from: PRIMARY_BLUE, to: "#60a5fa" }, // Blue gradient（与 New Task 一致）
  { from: "#8b5cf6", to: "#a78bfa" }, // Purple gradient
  { from: "#10b981", to: "#34d399" }, // Green gradient
  { from: "#f59e0b", to: "#fbbf24" }, // Amber gradient
  { from: "#ef4444", to: "#f87171" }, // Red gradient
  { from: "#06b6d4", to: "#22d3ee" }, // Cyan gradient
];

// 纯色板（用于 LINE 图等，主色与 New Task 一致）
const SOLID_PALETTE = [
  PRIMARY_BLUE, // Blue（与 New Task 一致）
  "#8b5cf6", // Purple
  "#10b981", // Green
  "#f59e0b", // Amber
  "#ef4444", // Red
  "#06b6d4", // Cyan
];

// 饼图专用配色（主色与 New Task 一致）
const PIE_COLORS = [PRIMARY_BLUE, "#a855f7", "#f97316", "#ef4444", "#10b981"];

const formatNumber = (value, options = {}) => {
  if (value === null || value === undefined || isNaN(value)) {
    return "—";
  }
  if (typeof value === "string") {
    const num = Number(value);
    if (isNaN(num)) return value;
    value = num;
  }

  // 默认显示完整数值（不使用K/M缩写，保留所有小数位）
  const { useAbbreviation = false, maxDecimals = null } = options;

  if (useAbbreviation) {
    // 仅在明确要求缩写时才使用K/M格式
    if (Math.abs(value) >= 1000000) {
      return `${(value / 1000000).toFixed(1)}M`;
    }
    if (Math.abs(value) >= 1000) {
      return `${(value / 1000).toFixed(1)}K`;
    }
  }

  // 显示完整数值
  if (maxDecimals !== null) {
    // 如果指定了最大小数位数，则使用它
    return value.toLocaleString("en-US", {
      minimumFractionDigits: 0,
      maximumFractionDigits: maxDecimals
    });
  } else {
    // 否则显示原始精度（完整数值）
    return value.toString();
  }
};

// 自定义 Tooltip - 仅显示未隐藏的系列（基于filteredData）
const CustomTooltip = ({ active, payload, label, hiddenSeries }) => {
  if (!active || !payload || !payload.length) {
    return null;
  }

  // 将hiddenSeries转换为Set以便快速查找
  const hiddenSet = hiddenSeries instanceof Set
    ? hiddenSeries
    : new Set(Array.isArray(hiddenSeries) ? hiddenSeries : []);

  // 过滤掉隐藏的系列
  const visiblePayload = payload.filter(
    (entry) => !hiddenSet.has(entry.dataKey)
  );

  if (visiblePayload.length === 0) {
    return null;
  }

  return (
    <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-[160px]">
      <p className="text-sm font-semibold text-gray-900 mb-2 border-b border-slate-100 pb-2">
        {label}
      </p>
      <div className="space-y-1.5">
        {visiblePayload.map((entry, index) => (
          <div key={index} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <div
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-xs text-slate-600">{entry.name}</span>
            </div>
            <span className="text-xs font-semibold text-gray-900">
              {formatNumber(entry.value, { maxDecimals: 10 })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
};

// 自定义图例组件 - 统一 spacing，支持自动换行
// 图例Cell永久保留，不隐藏DOM元素。隐藏时opacity为0.28，未隐藏时opacity为1
const CustomLegendContent = ({ payload, onClick, hiddenSeries, iconType = "square" }) => {
  // 安全处理 payload
  const safePayload = Array.isArray(payload) ? payload : [];
  const payloadLength = safePayload.length;

  // 现在可以安全地进行条件返回
  if (payloadLength === 0) return null;

  // 确保 iconType 正确传递，明确检查字符串值
  const effectiveIconType = (iconType === "circle" || iconType === "Circle") ? "circle" : "square";

  // 将hiddenSeries转换为Set以便快速查找
  const hiddenSet = hiddenSeries instanceof Set
    ? hiddenSeries
    : new Set(Array.isArray(hiddenSeries) ? hiddenSeries : []);

  // Expense和Revenue的颜色映射
  const getSeriesColor = (seriesName, defaultColor) => {
    const normalizedName = (seriesName || "").toLowerCase();
    if (normalizedName === "expense") {
      return "#f59e0b"; // 橙色
    }
    if (normalizedName === "revenue") {
      return PRIMARY_BLUE; // 蓝色（与 New Task 一致）
    }
    return defaultColor || PRIMARY_BLUE;
  };

  // 渲染单个图例项
  const renderLegendItem = (entry, index) => {
    const dataKey = entry.dataKey || entry.id || entry.value || entry.name;
    const seriesName = entry.value || entry.name || dataKey;
    // 判断是否隐藏
    const isHidden = hiddenSet.has(dataKey);
    // 使用稳定的 key，基于 dataKey 而不是 index
    const stableKey = dataKey || `legend-item-${index}`;
    // 图例视觉元素opacity：隐藏时为0.28，未隐藏时为1
    const legendOpacity = isHidden ? 0.28 : 1;
    // 判断是否为Expense或Revenue系列
    const normalizedName = (seriesName || "").toLowerCase();
    const isExpenseOrRevenue = normalizedName === "expense" || normalizedName === "revenue";
    // 获取系列对应的颜色
    const seriesColor = getSeriesColor(seriesName, entry.color);

    return (
      <button
        key={stableKey}
        onClick={(e) => {
          e.stopPropagation();
          e.preventDefault();
          onClick?.(entry);
        }}
        className="flex items-center transition-all hover:opacity-80"
        style={{
          padding: 0,
          marginRight: "16px", // 横向间距：16px（Ant Design 紧凑间距标准）
          marginBottom: "8px", // 纵向间距：8px（两行之间的间距）
          cursor: "pointer",
          background: "none",
          border: "none",
          flexShrink: 0
        }}
      >
        {/* 为Expense和Revenue添加矩形色块 */}
        {isExpenseOrRevenue && (
          <div
            className="flex-shrink-0"
            style={{
              width: "12px",
              height: "12px",
              backgroundColor: seriesColor,
              opacity: legendOpacity,
              borderRadius: "2px",
              marginRight: "6px" // 标记与文字间距：6px
            }}
          />
        )}
        {/* 原有的图标（非Expense/Revenue时显示） */}
        {!isExpenseOrRevenue && (
          effectiveIconType === "circle" ? (
            <div
              className="rounded-full flex-shrink-0"
              style={{
                width: "12px",
                height: "12px",
                backgroundColor: entry.color || PRIMARY_BLUE,
                opacity: legendOpacity,
                border: "none",
                marginRight: "6px" // 标记与文字间距：6px
              }}
            />
          ) : (
            <div
              className="flex-shrink-0"
              style={{
                width: "12px",
                height: "12px",
                backgroundColor: entry.color || PRIMARY_BLUE,
                opacity: legendOpacity,
                border: "none",
                marginRight: "6px" // 标记与文字间距：6px
              }}
            />
          )
        )}
        {/* 文字样式：符合 Ant Design 规范（14px 正文大小 + 85% 黑色） */}
        <span
          style={{
            fontSize: "14px",
            color: "rgba(0, 0, 0, 0.85)", // Ant Design 正文颜色（85% 黑色）
            fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
            lineHeight: "22px"
          }}
        >
          {entry.value}
        </span>
      </button>
    );
  };

  return (
    <div style={{ width: "100%" }}>
      {/* 图例项容器 - 自动换行布局（直接换行，不使用分页） */}
      <div
        className="flex flex-wrap justify-center items-start"
        style={{
          width: "100%",
          paddingTop: "16px",
          paddingBottom: "0"
        }}
      >
        {safePayload.map((entry, index) => renderLegendItem(entry, index))}
      </div>
    </div>
  );
};

// 计算过滤后的数据（用于计算：轴域、tooltip、百分比等）
// allData: 原始数据数组
// hiddenSeries: 隐藏的系列名称数组或Set
const computeFilteredData = (allData, hiddenSeries) => {
  if (!allData || !Array.isArray(allData) || allData.length === 0) {
    return [];
  }

  // 将hiddenSeries转换为Set以便快速查找
  let hiddenSet;
  if (hiddenSeries instanceof Set) {
    hiddenSet = hiddenSeries;
  } else if (Array.isArray(hiddenSeries)) {
    hiddenSet = new Set(hiddenSeries);
  } else {
    hiddenSet = new Set();
  }

  // 过滤数据：只保留未隐藏的系列
  const filtered = allData.map((item) => {
    const filteredItem = { name: item.name };
    // 只包含未隐藏的系列数据
    Object.keys(item).forEach((key) => {
      if (key !== 'name' && !hiddenSet.has(key)) {
        filteredItem[key] = item[key];
      }
    });
    return filteredItem;
  });

  return filtered;
};

// 计算过滤后的饼图数据（用于计算百分比、total等）
// allPieData: 原始饼图数据数组 [{name: "Apr", value: 100}, ...]
// hiddenSlices: 隐藏的切片名称数组或Set
const computeFilteredPieData = (allPieData, hiddenSlices) => {
  if (!allPieData || !Array.isArray(allPieData) || allPieData.length === 0) {
    return [];
  }

  // 将hiddenSlices转换为Set以便快速查找
  let hiddenSet;
  if (hiddenSlices instanceof Set) {
    hiddenSet = hiddenSlices;
  } else if (Array.isArray(hiddenSlices)) {
    hiddenSet = new Set(hiddenSlices);
  } else {
    hiddenSet = new Set();
  }

  // 过滤数据：只保留未隐藏的切片
  const filtered = allPieData.filter((entry) => !hiddenSet.has(entry.name));

  return filtered;
};

// Bar Chart 组件
const BarChartComponent = ({ data, series, palette }) => {
  const [hiddenSeries, setHiddenSeries] = useState(new Set());

  // 所有数据（用于渲染，保持完整）
  const allChartData = useMemo(() => {
    return data.labels?.map((label, idx) => {
      const item = { name: label };
      series.forEach((s) => {
        item[s.name] = s.values?.[idx] ?? 0;
      });
      return item;
    }) || [];
  }, [data.labels, series]);

  // 将hiddenSeries转换为数组格式
  const hiddenSeriesArray = useMemo(() => {
    return Array.from(hiddenSeries);
  }, [hiddenSeries]);

  // 过滤后的数据（用于计算：Y轴domain、tooltip等）
  const filteredChartData = useMemo(() => {
    const filtered = computeFilteredData(allChartData, hiddenSeriesArray);
    return filtered;
  }, [allChartData, hiddenSeriesArray]);

  // 提取filteredChartData中所有有效数值（重构计算逻辑）
  const allValidValues = useMemo(() => {
    if (!filteredChartData || filteredChartData.length === 0) {
      return [];
    }

    const values = [];
    filteredChartData.forEach((item) => {
      Object.keys(item).forEach((key) => {
        // 排除'name'字段，只处理数值类型的系列数据
        if (key !== 'name' && item[key] !== undefined) {
          const value = Number(item[key]);
          if (!isNaN(value) && isFinite(value) && value >= 0) {
            values.push(value);
          }
        }
      });
    });


    return values;
  }, [filteredChartData]);

  // 计算合理的刻度值（4-6个主要刻度，更密集且均匀）
  const calculateNiceTicks = (min, max, targetTicks = 5) => {
    const range = max - min;
    if (range === 0) return [min, max];

    // 计算理想的步长（目标4-6个刻度）
    const rawStep = range / (targetTicks - 1);

    // 将步长调整为"友好"的数字（10的幂次方的倍数）
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalizedStep = rawStep / magnitude;

    // 选择最接近的友好步长（1, 1.25, 2, 2.5, 5, 10的倍数）
    // 增加2.5和1.25选项，使刻度更密集且友好（如2.0K、2.5K间隔）
    let niceStep;
    if (normalizedStep <= 0.6) niceStep = 0.5 * magnitude;
    else if (normalizedStep <= 1.0) niceStep = 1 * magnitude;
    else if (normalizedStep <= 1.5) niceStep = 1.25 * magnitude;
    else if (normalizedStep <= 2.0) niceStep = 2 * magnitude;
    else if (normalizedStep <= 3.0) niceStep = 2.5 * magnitude;
    else if (normalizedStep <= 5.0) niceStep = 5 * magnitude;
    else niceStep = 10 * magnitude;

    // 计算起始点（向下取整到niceStep的倍数，但确保从0或接近0开始）
    let niceMin;
    if (min <= 0) {
      niceMin = 0;
    } else {
      niceMin = Math.floor(min / niceStep) * niceStep;
    }

    // 计算结束点（向上取整到niceStep的倍数）
    const niceMax = Math.ceil(max / niceStep) * niceStep;

    // 生成刻度值
    const ticks = [];
    for (let value = niceMin; value <= niceMax + niceStep * 0.01; value += niceStep) {
      // 避免浮点数精度问题
      const roundedValue = Math.round(value * 100) / 100;
      if (roundedValue <= niceMax + niceStep * 0.01) {
        ticks.push(roundedValue);
      }
    }

    // 确保最大值包含在内（如果不在刻度中）
    if (ticks.length > 0 && ticks[ticks.length - 1] < max) {
      const lastTick = Math.ceil(max / niceStep) * niceStep;
      if (!ticks.includes(lastTick)) {
        ticks.push(Math.round(lastTick * 100) / 100);
      }
    }

    // 确保刻度数量在4-6个之间
    if (ticks.length < 4) {
      // 如果刻度太少，减小步长
      const smallerStep = niceStep / 2;
      ticks.length = 0;
      for (let value = niceMin; value <= niceMax + smallerStep * 0.01; value += smallerStep) {
        const roundedValue = Math.round(value * 100) / 100;
        if (roundedValue <= niceMax + smallerStep * 0.01) {
          ticks.push(roundedValue);
        }
      }
    } else if (ticks.length > 6) {
      // 如果刻度太多，增大步长
      const largerStep = niceStep * 2;
      ticks.length = 0;
      for (let value = niceMin; value <= niceMax + largerStep * 0.01; value += largerStep) {
        const roundedValue = Math.round(value * 100) / 100;
        if (roundedValue <= niceMax + largerStep * 0.01) {
          ticks.push(roundedValue);
        }
      }
    }

    return ticks;
  };

  // 基于allValidValues计算Y轴domain（最大值和最小值）
  const yAxisDomainConfig = useMemo(() => {
    if (allValidValues.length === 0) {
      return {
        min: 0,
        max: 100,
        domain: [0, 100],
        ticks: [0, 25, 50, 75, 100]
      };
    }

    const actualMin = Math.min(...allValidValues);
    const actualMax = Math.max(...allValidValues);

    // 扩展10%，确保不小于0
    const adjustedMin = Math.max(actualMin * 0.9, 0);
    const adjustedMax = actualMax * 1.1;

    // 计算合理的刻度值（4-6个主要刻度，更密集且均匀）
    const ticks = calculateNiceTicks(adjustedMin, adjustedMax, 5);

    // 确保最大值显示在顶部
    const finalMax = Math.max(adjustedMax, ticks[ticks.length - 1] || adjustedMax);

    return {
      min: adjustedMin,
      max: finalMax,
      domain: [adjustedMin, finalMax],
      ticks: ticks
    };
  }, [allValidValues, filteredChartData, hiddenSeries]);

  // 直接使用数组形式的domain，但确保每次都是新数组引用
  const yAxisDomain = useMemo(() => {
    const { min, max } = yAxisDomainConfig;
    const domain = [min, max];

    // 返回数组形式，但每次创建新数组确保引用不同
    return domain;
  }, [yAxisDomainConfig]);

  const handleLegendClick = (entry) => {
    // 支持多种可能的 dataKey 来源
    const dataKey = entry?.dataKey || entry?.id || entry?.value || entry?.name;
    if (dataKey) {
      setHiddenSeries((prev) => {
        const next = new Set(prev);
        if (next.has(dataKey)) {
          next.delete(dataKey);
        } else {
          next.add(dataKey);
        }
        return next;
      });
    }
  };

  return (
    <div
      className="w-full bg-white rounded-xl"
      style={{
        padding: "24px",
        borderRadius: "12px",
        boxShadow: "rgba(0,0,0,0.05) 0px 1px 4px",
      }}
    >
      <ResponsiveContainer
        key={`bar-container-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
        width="100%"
        height={400}
      >
        <BarChart
          key={`bar-chart-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
          data={allChartData}
          margin={{ top: 16, right: 24, bottom: 16, left: 24 }}
        >
          <defs>
            {series.map((s, idx) => {
              return (
                <linearGradient
                  key={s.name}
                  id={`gradientBar-${s.name}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                >
                  <stop
                    offset="0%"
                    stopColor={
                      palette?.[idx % palette.length]?.from ||
                      GRADIENT_PALETTE[idx % GRADIENT_PALETTE.length].from
                    }
                    stopOpacity={1}
                  />
                  <stop
                    offset="100%"
                    stopColor={
                      palette?.[idx % palette.length]?.to ||
                      GRADIENT_PALETTE[idx % GRADIENT_PALETTE.length].to
                    }
                    stopOpacity={0.8}
                  />
                </linearGradient>
              );
            })}
          </defs>
          <CartesianGrid stroke="#eee" strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            tickSize={8}
            tickMargin={12}
            dy={12}
            stroke="#999"
            fontSize={12}
            angle={-45}
            textAnchor="end"
            height={80}
          />
          <YAxis
            key={`y-axis-bar-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
            width={40}
            tickMargin={8}
            fontSize={12}
            stroke="#999"
            tickFormatter={(value) => formatNumber(value, { useAbbreviation: true })}
            domain={[yAxisDomainConfig.min, yAxisDomainConfig.max]}
            allowDataOverflow={true}
            type="number"
            scale="linear"
            allowDecimals={true}
            ticks={yAxisDomainConfig.ticks}
            interval={0}
          />
          <Tooltip content={<CustomTooltip hiddenSeries={hiddenSeries} />} />
          <Legend
            content={
              <CustomLegendContent
                payload={series.map((s, idx) => ({
                  value: s.name,
                  name: s.name,
                  dataKey: s.name,
                  color:
                    palette?.[idx % palette.length]?.from ||
                    GRADIENT_PALETTE[idx % GRADIENT_PALETTE.length].from,
                }))}
                onClick={handleLegendClick}
                hiddenSeries={hiddenSeries}
                iconType="circle"
              />
            }
            height={32}
          />
          {/* 渲染所有系列（基于allChartData），但使用视觉样式控制 */}
          {/* 隐藏系列的fillOpacity为0（不显示），未隐藏的为1 */}
          {series.map((s, idx) => {
            const isHidden = hiddenSeries.has(s.name);
            return (
              <Bar
                key={s.name}
                dataKey={s.name}
                fill={`url(#gradientBar-${s.name})`}
                radius={[6, 6, 0, 0]}
                barSize={24}
                fillOpacity={isHidden ? 0 : 1}
                animationDuration={800}
                animationBegin={idx * 100}
              />
            );
          })}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};

// Line Chart 组件
const LineChartComponent = ({ data, series, palette }) => {
  const [hiddenSeries, setHiddenSeries] = useState(new Set());

  // 所有数据（用于渲染，保持完整）
  const allChartData = useMemo(() => {
    return data.labels?.map((label, idx) => {
      const item = { name: label };
      series.forEach((s) => {
        item[s.name] = s.values?.[idx] ?? 0;
      });
      return item;
    }) || [];
  }, [data.labels, series]);

  // 将hiddenSeries转换为数组格式
  const hiddenSeriesArray = useMemo(() => {
    return Array.from(hiddenSeries);
  }, [hiddenSeries]);

  // 过滤后的数据（用于计算：Y轴domain、tooltip等）
  const filteredChartData = useMemo(() => {
    const filtered = computeFilteredData(allChartData, hiddenSeriesArray);
    return filtered;
  }, [allChartData, hiddenSeriesArray]);

  // 提取filteredChartData中所有有效数值（重构计算逻辑）
  const allValidValues = useMemo(() => {
    if (!filteredChartData || filteredChartData.length === 0) {
      return [];
    }

    const values = [];
    filteredChartData.forEach((item) => {
      Object.keys(item).forEach((key) => {
        // 排除'name'字段，只处理数值类型的系列数据
        if (key !== 'name' && item[key] !== undefined) {
          const value = Number(item[key]);
          if (!isNaN(value) && isFinite(value) && value >= 0) {
            values.push(value);
          }
        }
      });
    });


    return values;
  }, [filteredChartData]);

  // 计算合理的刻度值（4-6个主要刻度，更密集且均匀）
  const calculateNiceTicks = (min, max, targetTicks = 5) => {
    const range = max - min;
    if (range === 0) return [min, max];

    // 计算理想的步长（目标4-6个刻度）
    const rawStep = range / (targetTicks - 1);

    // 将步长调整为"友好"的数字（10的幂次方的倍数）
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalizedStep = rawStep / magnitude;

    // 选择最接近的友好步长（1, 1.25, 2, 2.5, 5, 10的倍数）
    // 增加2.5和1.25选项，使刻度更密集且友好（如2.0K、2.5K间隔）
    let niceStep;
    if (normalizedStep <= 0.6) niceStep = 0.5 * magnitude;
    else if (normalizedStep <= 1.0) niceStep = 1 * magnitude;
    else if (normalizedStep <= 1.5) niceStep = 1.25 * magnitude;
    else if (normalizedStep <= 2.0) niceStep = 2 * magnitude;
    else if (normalizedStep <= 3.0) niceStep = 2.5 * magnitude;
    else if (normalizedStep <= 5.0) niceStep = 5 * magnitude;
    else niceStep = 10 * magnitude;

    // 计算起始点（向下取整到niceStep的倍数，但确保从0或接近0开始）
    let niceMin;
    if (min <= 0) {
      niceMin = 0;
    } else {
      niceMin = Math.floor(min / niceStep) * niceStep;
    }

    // 计算结束点（向上取整到niceStep的倍数）
    const niceMax = Math.ceil(max / niceStep) * niceStep;

    // 生成刻度值
    const ticks = [];
    for (let value = niceMin; value <= niceMax + niceStep * 0.01; value += niceStep) {
      // 避免浮点数精度问题
      const roundedValue = Math.round(value * 100) / 100;
      if (roundedValue <= niceMax + niceStep * 0.01) {
        ticks.push(roundedValue);
      }
    }

    // 确保最大值包含在内（如果不在刻度中）
    if (ticks.length > 0 && ticks[ticks.length - 1] < max) {
      const lastTick = Math.ceil(max / niceStep) * niceStep;
      if (!ticks.includes(lastTick)) {
        ticks.push(Math.round(lastTick * 100) / 100);
      }
    }

    // 确保刻度数量在4-6个之间
    if (ticks.length < 4) {
      // 如果刻度太少，减小步长
      const smallerStep = niceStep / 2;
      ticks.length = 0;
      for (let value = niceMin; value <= niceMax + smallerStep * 0.01; value += smallerStep) {
        const roundedValue = Math.round(value * 100) / 100;
        if (roundedValue <= niceMax + smallerStep * 0.01) {
          ticks.push(roundedValue);
        }
      }
    } else if (ticks.length > 6) {
      // 如果刻度太多，增大步长
      const largerStep = niceStep * 2;
      ticks.length = 0;
      for (let value = niceMin; value <= niceMax + largerStep * 0.01; value += largerStep) {
        const roundedValue = Math.round(value * 100) / 100;
        if (roundedValue <= niceMax + largerStep * 0.01) {
          ticks.push(roundedValue);
        }
      }
    }

    return ticks;
  };

  // 基于allValidValues计算Y轴domain（最大值和最小值）
  const yAxisDomainConfig = useMemo(() => {
    if (allValidValues.length === 0) {
      return {
        min: 0,
        max: 100,
        domain: [0, 100],
        ticks: [0, 25, 50, 75, 100]
      };
    }

    const actualMin = Math.min(...allValidValues);
    const actualMax = Math.max(...allValidValues);

    // 扩展10%，确保不小于0
    const adjustedMin = Math.max(actualMin * 0.9, 0);
    const adjustedMax = actualMax * 1.1;

    // 计算合理的刻度值（4-6个主要刻度，更密集且均匀）
    const ticks = calculateNiceTicks(adjustedMin, adjustedMax, 5);

    // 确保最大值显示在顶部
    const finalMax = Math.max(adjustedMax, ticks[ticks.length - 1] || adjustedMax);

    return {
      min: adjustedMin,
      max: finalMax,
      domain: [adjustedMin, finalMax],
      ticks: ticks
    };
  }, [allValidValues, filteredChartData, hiddenSeries]);

  // 直接使用数组形式的domain，但确保每次都是新数组引用
  // 同时使用函数形式作为fallback，确保Recharts使用我们的值
  const yAxisDomain = useMemo(() => {
    const { min, max } = yAxisDomainConfig;

    // 返回数组形式，但每次创建新数组确保引用不同
    return [min, max];
  }, [yAxisDomainConfig]);

  // 计算线宽：单折线图 2.5-3px，多折线图默认 2px
  const strokeWidth = useMemo(() => {
    const visibleCount = series.length - hiddenSeries.size;
    if (visibleCount === 1) {
      return 2.5; // 单折线图
    }
    return 2; // 默认
  }, [series.length, hiddenSeries.size]);

  const handleLegendClick = (entry) => {
    const dataKey = entry?.dataKey || entry?.id;
    if (dataKey) {
      setHiddenSeries((prev) => {
        const next = new Set(prev);
        if (next.has(dataKey)) {
          next.delete(dataKey);
        } else {
          next.add(dataKey);
        }
        return next;
      });
    }
  };

  return (
    <div
      className="w-full bg-white rounded-xl"
      style={{
        padding: "24px",
        borderRadius: "12px",
        boxShadow: "rgba(0,0,0,0.05) 0px 1px 4px",
      }}
    >
      <ResponsiveContainer
        key={`line-container-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
        width="100%"
        height={400}
      >
        <LineChart
          key={`line-chart-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
          data={allChartData}
          margin={{ top: 16, right: 24, bottom: 16, left: 24 }}
          syncId={undefined}
        >
          <CartesianGrid stroke="#eee" strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            tickSize={8}
            tickMargin={12}
            dy={12}
            stroke="#999"
            fontSize={12}
            angle={-45}
            textAnchor="end"
            height={80}
          />
          <YAxis
            key={`y-axis-line-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
            width={40}
            tickMargin={8}
            fontSize={12}
            stroke="#999"
            tickFormatter={(value) => formatNumber(value, { useAbbreviation: true })}
            domain={[yAxisDomainConfig.min, yAxisDomainConfig.max]}
            allowDataOverflow={true}
            type="number"
            scale="linear"
            allowDecimals={true}
            ticks={yAxisDomainConfig.ticks}
            interval={0}
          />
          <Tooltip content={<CustomTooltip hiddenSeries={hiddenSeries} />} />
          <Legend
            content={
              <CustomLegendContent
                payload={series.map((s, idx) => ({
                  value: s.name,
                  dataKey: s.name,
                  color:
                    palette?.[idx % palette.length]?.from ||
                    SOLID_PALETTE[idx % SOLID_PALETTE.length],
                }))}
                onClick={handleLegendClick}
                hiddenSeries={hiddenSeries}
                iconType="circle"
              />
            }
            height={32}
          />
          {/* 渲染所有系列（基于allChartData），但使用视觉样式控制 */}
          {/* 隐藏系列的strokeOpacity为0（不显示），未隐藏的为1 */}
          {series.map((s, idx) => {
            const isHidden = hiddenSeries.has(s.name);
            const color =
              palette?.[idx % palette.length]?.from ||
              SOLID_PALETTE[idx % SOLID_PALETTE.length];
            return (
              <Line
                key={s.name}
                type="monotone"
                dataKey={s.name}
                stroke={color}
                strokeWidth={strokeWidth}
                strokeOpacity={isHidden ? 0 : 1}
                dot={{
                  r: 4,
                  fill: color,
                  fillOpacity: isHidden ? 0 : 1,
                  stroke: "#fff",
                  strokeWidth: 2
                }}
                activeDot={{
                  r: 6,
                  fill: color,
                  stroke: "#fff",
                  strokeWidth: 2
                }}
                animationDuration={800}
                animationBegin={idx * 100}
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

// Pie Chart 组件
const PieChartComponent = ({ data, series, palette }) => {
  // 使用 Set 存储隐藏的系列，与 Bar/Line 保持一致
  const [hiddenSeries, setHiddenSeries] = useState(new Set());
  const primarySeries = series[0];

  // 所有数据（用于渲染，保持完整）
  const allPieData = useMemo(() => {
    return (
      data.labels
        ?.map((label, idx) => ({
          name: label,
          value: primarySeries?.values?.[idx] ?? 0,
        }))
        .filter((item) => item.value > 0) || []
    );
  }, [data.labels, primarySeries]);

  // 将hiddenSeries转换为数组格式
  const hiddenSeriesArray = useMemo(() => {
    return Array.from(hiddenSeries);
  }, [hiddenSeries]);

  // 过滤后的数据（用于计算：百分比、total等）
  // 使用computeFilteredPieData函数，确保隐藏切片不参与计算
  const filteredPieData = useMemo(() => {
    return computeFilteredPieData(allPieData, hiddenSeries);
  }, [allPieData, hiddenSeries]);

  // 动态计算总数（仅基于未隐藏的切片，排除隐藏切片）
  const totalValue = useMemo(
    () => filteredPieData.reduce((sum, item) => sum + item.value, 0),
    [filteredPieData]
  );

  // 为每个切片添加计算后的百分比（基于filteredPieData的total）
  // 隐藏切片的百分比为0，未隐藏切片的百分比基于新的total重新计算
  const pieDataWithPercent = useMemo(() => {
    const result = allPieData.map((slice) => {
      const isHidden = hiddenSeries.has(slice.name);
      // 隐藏切片百分比为0，未隐藏切片基于filteredPieData的total计算
      const percent = isHidden ? 0 : (totalValue > 0 ? (slice.value / totalValue) * 100 : 0);

      return {
        ...slice,
        percent,
        isHidden,
      };
    });

    return result;
  }, [allPieData, totalValue, hiddenSeries]);

  const handleLegendClick = (entry) => {
    // 支持多种可能的 dataKey 来源
    const dataKey = entry?.dataKey || entry?.id || entry?.value || entry?.name;
    if (dataKey) {
      setHiddenSeries((prev) => {
        const next = new Set(prev);
        if (next.has(dataKey)) {
          next.delete(dataKey);
        } else {
          next.add(dataKey);
        }
        return next;
      });
    }
  };

  // 自定义 Label - 显示百分比（基于filteredPieData计算）
  // 隐藏切片不显示标签，仅通过低透明度视觉呈现
  const renderCustomLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, name, value, payload }) => {
    // 从payload中获取计算好的百分比
    const sliceData = pieDataWithPercent.find(item => item.name === name);
    const isHidden = sliceData?.isHidden || hiddenSeries.has(name);

    // 如果切片被隐藏，不显示任何标签（仅通过低透明度视觉呈现，与Line/Bar逻辑一致）
    if (isHidden) {
      return null;
    }

    // 使用预先计算好的百分比（基于filteredPieData的total）
    const filteredPercent = sliceData?.percent || 0;

    // 只显示大于 3% 的标签，避免过于拥挤
    if (filteredPercent < 3) return null;

    const RADIAN = Math.PI / 180;
    const radius = innerRadius + (outerRadius - innerRadius) * 0.5;
    const x = cx + radius * Math.cos(-midAngle * RADIAN);
    const y = cy + radius * Math.sin(-midAngle * RADIAN);

    return (
      <text
        x={x}
        y={y}
        fill="white"
        textAnchor={x > cx ? "start" : "end"}
        dominantBaseline="central"
        fontSize={12}
        fontWeight="600"
        className="drop-shadow-sm"
      >
        {`${filteredPercent.toFixed(0)}%`}
      </text>
    );
  };

  // 自定义 Pie Tooltip - 显示可见系列的百分比（基于 filteredPieData 计算）
  // 隐藏切片显示"已隐藏，不参与计算"，与Line/Bar逻辑对齐
  const PieCustomTooltip = ({ active, payload, totalValue, hiddenSeries, pieDataWithPercent }) => {
    if (!active || !payload || !payload.length) {
      return null;
    }

    const entry = payload[0];
    const name = entry.name;
    const value = entry.value;

    // 将hiddenSeries转换为Set以便快速查找
    const hiddenSet = hiddenSeries instanceof Set
      ? hiddenSeries
      : new Set(Array.isArray(hiddenSeries) ? hiddenSeries : []);
    const isHidden = hiddenSet.has(name);

    // 如果隐藏，显示"已隐藏，不参与计算"提示（与Line/Bar逻辑对齐）
    if (isHidden) {
      return (
        <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-[160px]">
          <p className="text-sm font-semibold text-gray-500 mb-2 border-b border-slate-100 pb-2">
            {name}
          </p>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">Value</span>
            <span className="text-xs font-semibold text-gray-500">
              {formatNumber(value, { maxDecimals: 10 })}
            </span>
          </div>
          <div className="mt-2 pt-2 border-t border-slate-100">
            <span className="text-xs text-slate-400 italic">已隐藏，不参与计算</span>
          </div>
        </div>
      );
    }

    // 从pieDataWithPercent中获取预先计算好的百分比（基于filteredPieData的total）
    const sliceData = pieDataWithPercent.find(item => item.name === name);
    const percent = sliceData?.percent || (totalValue > 0 ? ((value / totalValue) * 100) : 0);

    return (
      <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-[160px]">
        <p className="text-sm font-semibold text-gray-900 mb-2 border-b border-slate-100 pb-2">
          {name}
        </p>
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-slate-600">Value</span>
          <span className="text-xs font-semibold text-gray-900">
            {formatNumber(value, { maxDecimals: 10 })} ({percent.toFixed(1)}%)
          </span>
        </div>
      </div>
    );
  };

  return (
    <div
      className="w-full bg-white rounded-xl"
      style={{
        padding: "24px",
        borderRadius: "12px",
        boxShadow: "rgba(0,0,0,0.05) 0px 1px 4px",
      }}
    >
      <ResponsiveContainer width="100%" height={320}>
        <PieChart margin={{ top: 16, right: 24, bottom: 24, left: 24 }}>
          <Tooltip
            content={<PieCustomTooltip
              totalValue={totalValue}
              hiddenSeries={hiddenSeries}
              pieDataWithPercent={pieDataWithPercent}
            />}
          />
          {/* 使用 pieDataWithPercent 渲染所有切片，包含计算好的百分比 */}
          <Pie
            data={pieDataWithPercent}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius="45%"
            outerRadius="70%"
            paddingAngle={2}
            stroke="#fff"
            strokeWidth={2}
            label={renderCustomLabel}
            labelLine={false}
            animationDuration={800}
          >
            {/* 渲染所有切片（基于pieDataWithPercent），但使用视觉样式控制 */}
            {/* 隐藏切片的fillOpacity为0.28（灰化），未隐藏的为1 */}
            {pieDataWithPercent.map((entry, index) => {
              return (
                <Cell
                  key={`cell-${entry.name}-${index}`}
                  fill={PIE_COLORS[index % PIE_COLORS.length]}
                  fillOpacity={entry.isHidden ? 0.28 : 1}
                />
              );
            })}
          </Pie>
          <Legend
            content={
              <CustomLegendContent
                payload={pieDataWithPercent.map((entry, index) => ({
                  value: entry.name,
                  name: entry.name,
                  type: 'circle',
                  id: entry.name,
                  dataKey: entry.name,
                  color: PIE_COLORS[index % PIE_COLORS.length],
                }))}
                onClick={handleLegendClick}
                hiddenSeries={hiddenSeries}
                iconType="circle"
              />
            }
            height={32}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};

// 主组件
const RechartsVisualization = ({ chartData, chartType, palette }) => {
  const series = chartData?.series || [];

  // 转换 palette 格式（如果传入的是字符串数组，转换为渐变对象）
  const normalizedPalette = useMemo(() => {
    if (!palette || !Array.isArray(palette)) {
      return GRADIENT_PALETTE;
    }
    return palette.map((color) => {
      if (typeof color === "string") {
        return { from: color, to: color };
      }
      return color;
    });
  }, [palette]);

  if (!chartData?.series || !chartData.series.length) {
    return (
      <div className="flex items-center justify-center h-64">
        <p className="text-sm text-slate-500">Visualization data is unavailable.</p>
      </div>
    );
  }

  switch (chartType) {
    case "bar":
      return (
        <BarChartComponent
          data={chartData}
          series={series}
          palette={normalizedPalette}
        />
      );
    case "line":
      return (
        <LineChartComponent
          data={chartData}
          series={series}
          palette={normalizedPalette}
        />
      );
    case "pie":
      return (
        <PieChartComponent
          data={chartData}
          series={series}
          palette={normalizedPalette}
        />
      );
    default:
      return (
        <div className="flex items-center justify-center h-64">
          <p className="text-sm text-slate-500">Unsupported chart type.</p>
        </div>
      );
  }
};

export default RechartsVisualization;

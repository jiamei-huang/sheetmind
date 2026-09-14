import React, { useState, useMemo, useEffect, useRef } from "react";
import { RotateCcw } from "lucide-react";
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
import {
  categoryColor,
  pieVisibilityStats,
  toggleHiddenItem,
} from "../../utils/chartLegend";
import { buildYAxisScale } from "../../utils/chartAxis";
import { isTemporalChartAxis } from "../../utils/chartDisplay";
import {
  BRAND_PRIMARY,
  DATA_VIZ_COLORS,
  DATA_VIZ_GRADIENTS,
} from "../../constants/colors";

const PRIMARY_BLUE = BRAND_PRIMARY;
const GRADIENT_PALETTE = DATA_VIZ_GRADIENTS;
const SOLID_PALETTE = DATA_VIZ_COLORS;

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
      <p className="text-sm font-semibold text-slate-900 mb-2 border-b border-slate-100 pb-2">
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
            <span className="text-xs font-semibold text-slate-900">
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
const CustomLegendContent = ({
  payload,
  onClick,
  onReset,
  hiddenKeys,
  iconType = "square",
}) => {
  // 安全处理 payload
  const safePayload = Array.isArray(payload) ? payload : [];
  const payloadLength = safePayload.length;

  // 现在可以安全地进行条件返回
  if (payloadLength === 0) return null;

  // 确保 iconType 正确传递，明确检查字符串值
  const effectiveIconType = (iconType === "circle" || iconType === "Circle") ? "circle" : "square";

  // 将hiddenSeries转换为Set以便快速查找
  const hiddenSet = hiddenKeys instanceof Set
    ? hiddenKeys
    : new Set(Array.isArray(hiddenKeys) ? hiddenKeys : []);

  // Expense和Revenue的颜色映射
  const getSeriesColor = (seriesName, defaultColor) => {
    const normalizedName = (seriesName || "").toLowerCase();
    if (normalizedName === "expense") {
      return DATA_VIZ_COLORS[1];
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
        type="button"
        aria-pressed={!isHidden}
        title={isHidden ? `Show ${seriesName}` : `Hide ${seriesName}`}
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
            color: isHidden ? "#94a3b8" : "rgba(0, 0, 0, 0.85)",
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
        {hiddenSet.size > 0 && onReset && (
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onReset();
            }}
            className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700"
            title="Show all legend items"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Reset
          </button>
        )}
      </div>
    </div>
  );
};

const CategoryLegend = ({
  labels,
  hiddenCategories,
  onToggle,
  onReset,
  colorForIndex,
}) => {
  const uniqueLabels = labels.filter(
    (label, index) => labels.indexOf(label) === index
  );

  if (uniqueLabels.length < 2) {
    return null;
  }

  return (
    <div className="max-h-32 overflow-y-auto px-2">
      <CustomLegendContent
        payload={uniqueLabels.map((label, index) => ({
          value: label,
          name: label,
          dataKey: label,
          color: colorForIndex(index, label),
        }))}
        onClick={(entry) => onToggle(entry.dataKey)}
        onReset={onReset}
        hiddenKeys={hiddenCategories}
        iconType="circle"
      />
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
const BarChartComponent = ({
  data,
  series,
  palette,
  hiddenCategories,
  onToggleCategory,
  onResetCategories,
  showCategoryLegend,
}) => {
  const [hiddenSeries, setHiddenSeries] = useState(new Set());
  const useCategoryColors = showCategoryLegend && series.length === 1;

  // 所有数据（用于渲染，保持完整）
  const allChartData = useMemo(() => {
    return data.labels?.map((label, idx) => {
      const item = { name: label };
      series.forEach((s) => {
        item[s.name] = s.values?.[idx] ?? 0;
      });
      return item;
    }).filter((item) => !hiddenCategories.has(item.name)) || [];
  }, [data.labels, hiddenCategories, series]);

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
          if (!isNaN(value) && isFinite(value)) {
            values.push(value);
          }
        }
      });
    });


    return values;
  }, [filteredChartData]);

  const yAxisDomainConfig = useMemo(
    () => buildYAxisScale(allValidValues, { includeZero: true }),
    [allValidValues]
  );

  const handleLegendClick = (entry) => {
    // 支持多种可能的 dataKey 来源
    const dataKey = entry?.dataKey || entry?.id || entry?.value || entry?.name;
    if (dataKey) {
      setHiddenSeries((prev) => toggleHiddenItem(
        prev,
        dataKey,
        series.map((item) => item.name)
      ));
    }
  };

  return (
    <div className="w-full bg-white">
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
          <CartesianGrid vertical={false} stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            tickMargin={12}
            dy={12}
            axisLine={{ stroke: "#cbd5e1" }}
            tickLine={false}
            stroke="#64748b"
            fontSize={12}
            angle={-45}
            textAnchor="end"
            height={80}
          />
          <YAxis
            key={`y-axis-bar-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
            width={40}
            tickMargin={8}
            axisLine={false}
            tickLine={false}
            fontSize={12}
            stroke="#64748b"
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
          {series.length > 1 && <Legend
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
                onReset={() => setHiddenSeries(new Set())}
                hiddenKeys={hiddenSeries}
                iconType="circle"
              />
            }
            height={32}
          />}
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
              >
                {useCategoryColors && allChartData.map((entry) => {
                  const originalIndex = data.labels?.indexOf(entry.name) ?? 0;
                  return (
                    <Cell
                      key={`${s.name}-${entry.name}`}
                      fill={categoryColor(entry.name, Math.max(originalIndex, 0))}
                    />
                  );
                })}
              </Bar>
            );
          })}
        </BarChart>
      </ResponsiveContainer>
      {showCategoryLegend && (
        <CategoryLegend
          labels={data.labels ?? []}
          hiddenCategories={hiddenCategories}
          onToggle={onToggleCategory}
          onReset={onResetCategories}
          colorForIndex={(index, label) => categoryColor(label, index)}
        />
      )}
    </div>
  );
};

// Line Chart 组件
const LineChartComponent = ({
  data,
  series,
  palette,
  hiddenCategories,
  onToggleCategory,
  onResetCategories,
  showCategoryLegend,
  includeZero,
}) => {
  const [hiddenSeries, setHiddenSeries] = useState(new Set());

  // 所有数据（用于渲染，保持完整）
  const allChartData = useMemo(() => {
    return data.labels?.map((label, idx) => {
      const item = { name: label };
      series.forEach((s) => {
        item[s.name] = s.values?.[idx] ?? 0;
      });
      return item;
    }).filter((item) => !hiddenCategories.has(item.name)) || [];
  }, [data.labels, hiddenCategories, series]);

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
          if (!isNaN(value) && isFinite(value)) {
            values.push(value);
          }
        }
      });
    });


    return values;
  }, [filteredChartData]);

  const yAxisDomainConfig = useMemo(
    () => buildYAxisScale(allValidValues, { includeZero }),
    [allValidValues, includeZero]
  );

  // 计算线宽：单折线图 2.5-3px，多折线图默认 2px
  const strokeWidth = useMemo(() => {
    const visibleCount = series.length - hiddenSeries.size;
    if (visibleCount === 1) {
      return 2.5; // 单折线图
    }
    return 2; // 默认
  }, [series.length, hiddenSeries.size]);

  const handleLegendClick = (entry) => {
    const dataKey = entry?.dataKey || entry?.id || entry?.value || entry?.name;
    if (dataKey) {
      setHiddenSeries((prev) => toggleHiddenItem(
        prev,
        dataKey,
        series.map((item) => item.name)
      ));
    }
  };

  return (
    <div className="w-full bg-white">
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
          <CartesianGrid vertical={false} stroke="#e2e8f0" strokeDasharray="3 3" />
          <XAxis
            dataKey="name"
            tickMargin={12}
            dy={12}
            axisLine={{ stroke: "#cbd5e1" }}
            tickLine={false}
            stroke="#64748b"
            fontSize={12}
            angle={-45}
            textAnchor="end"
            height={80}
          />
          <YAxis
            key={`y-axis-line-${yAxisDomainConfig.max}-${Array.from(hiddenSeries).sort().join('-')}`}
            width={40}
            tickMargin={8}
            axisLine={false}
            tickLine={false}
            fontSize={12}
            stroke="#64748b"
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
          {series.length > 1 && <Legend
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
                onReset={() => setHiddenSeries(new Set())}
                hiddenKeys={hiddenSeries}
                iconType="circle"
              />
            }
            height={32}
          />}
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
      {showCategoryLegend && (
        <CategoryLegend
          labels={data.labels ?? []}
          hiddenCategories={hiddenCategories}
          onToggle={onToggleCategory}
          onReset={onResetCategories}
          colorForIndex={() => PRIMARY_BLUE}
        />
      )}
    </div>
  );
};

// Pie Chart 组件
const PieChartComponent = ({
  data,
  series,
  hiddenCategories,
  onToggleCategory,
  onResetCategories,
  showCategoryLegend,
}) => {
  const primarySeries = series[0];

  // 所有数据（用于渲染，保持完整）
  const allPieData = useMemo(() => {
    return (
      data.labels
        ?.map((label, idx) => ({
          name: label,
          value: primarySeries?.values?.[idx] ?? 0,
          color: categoryColor(label, idx),
        }))
        .filter((item) => item.value > 0) || []
    );
  }, [data.labels, primarySeries]);

  // 过滤后的数据（用于计算：百分比、total等）
  // 使用computeFilteredPieData函数，确保隐藏切片不参与计算
  const filteredPieData = useMemo(() => {
    return computeFilteredPieData(allPieData, hiddenCategories);
  }, [allPieData, hiddenCategories]);

  const visibilityStats = useMemo(
    () => pieVisibilityStats(allPieData, hiddenCategories),
    [allPieData, hiddenCategories]
  );

  const pieDataWithPercent = useMemo(() => {
    return filteredPieData.map((slice) => ({
      ...slice,
      percent: visibilityStats.visibleTotal > 0
        ? (slice.value / visibilityStats.visibleTotal) * 100
        : 0,
      totalPercent: visibilityStats.fullTotal > 0
        ? (slice.value / visibilityStats.fullTotal) * 100
        : 0,
    }));
  }, [filteredPieData, visibilityStats]);

  const renderCustomLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, name }) => {
    // 从payload中获取计算好的百分比
    const sliceData = pieDataWithPercent.find(item => item.name === name);
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

  const PieCustomTooltip = ({ active, payload, pieDataWithPercent }) => {
    if (!active || !payload || !payload.length) {
      return null;
    }

    const entry = payload[0];
    const name = entry.name;
    const value = entry.value;

    const sliceData = pieDataWithPercent.find(item => item.name === name);
    const visiblePercent = sliceData?.percent || 0;
    const totalPercent = sliceData?.totalPercent || 0;

    return (
      <div className="bg-white border border-slate-200 rounded-lg shadow-lg p-3 min-w-[160px]">
        <p className="text-sm font-semibold text-slate-900 mb-2 border-b border-slate-100 pb-2">
          {name}
        </p>
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-slate-600">Value</span>
          <span className="text-xs font-semibold text-slate-900">
            {formatNumber(value, { maxDecimals: 10 })}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between gap-3">
          <span className="text-xs text-slate-600">Visible share</span>
          <span className="text-xs font-semibold text-slate-900">{visiblePercent.toFixed(1)}%</span>
        </div>
        {visibilityStats.hiddenCount > 0 && (
          <div className="mt-1 flex items-center justify-between gap-3">
            <span className="text-xs text-slate-500">Full-data share</span>
            <span className="text-xs font-medium text-slate-600">{totalPercent.toFixed(1)}%</span>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="w-full bg-white">
      {visibilityStats.hiddenCount > 0 && (
        <div className="mb-2 rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <span>
            Percentages use visible categories. {visibilityStats.hiddenCount} hidden
            {visibilityStats.hiddenCount === 1 ? " category accounts" : " categories account"}
            {` for ${visibilityStats.hiddenPercent.toFixed(1)}% of the full total.`}
          </span>
        </div>
      )}
      <ResponsiveContainer width="100%" height={320}>
        <PieChart margin={{ top: 16, right: 24, bottom: 24, left: 24 }}>
          <Tooltip
            content={<PieCustomTooltip
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
            {pieDataWithPercent.map((entry, index) => {
              return (
                <Cell
                  key={`cell-${entry.name}-${index}`}
                  fill={entry.color}
                />
              );
            })}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      {showCategoryLegend && (
        <CategoryLegend
          labels={data.labels ?? []}
          hiddenCategories={hiddenCategories}
          onToggle={onToggleCategory}
          onReset={onResetCategories}
          colorForIndex={(index, label) => categoryColor(label, index)}
        />
      )}
    </div>
  );
};

// 主组件
const RechartsVisualization = ({ chartData, chartType, palette }) => {
  const series = chartData?.series || [];
  const categoryKeys = useMemo(
    () => [...new Set(chartData?.labels ?? [])],
    [chartData?.labels]
  );
  const [hiddenCategories, setHiddenCategories] = useState(new Set());
  const showCategoryLegend = !isTemporalChartAxis(chartData) && categoryKeys.length > 1;

  useEffect(() => {
    const availableCategories = new Set(categoryKeys);
    setHiddenCategories((previous) => {
      const next = new Set(
        [...previous].filter((category) => availableCategories.has(category))
      );
      return next.size === previous.size ? previous : next;
    });
  }, [categoryKeys]);

  const handleCategoryToggle = (category) => {
    setHiddenCategories((previous) => toggleHiddenItem(
      previous,
      category,
      categoryKeys
    ));
  };

  const resetCategories = () => setHiddenCategories(new Set());

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
          hiddenCategories={hiddenCategories}
          onToggleCategory={handleCategoryToggle}
          onResetCategories={resetCategories}
          showCategoryLegend={showCategoryLegend && series.length === 1}
        />
      );
    case "line":
      return (
        <LineChartComponent
          data={chartData}
          series={series}
          palette={normalizedPalette}
          hiddenCategories={hiddenCategories}
          onToggleCategory={handleCategoryToggle}
          onResetCategories={resetCategories}
          showCategoryLegend={showCategoryLegend}
          includeZero={!isTemporalChartAxis(chartData)}
        />
      );
    case "pie":
      return (
        <PieChartComponent
          data={chartData}
          series={series}
          palette={normalizedPalette}
          hiddenCategories={hiddenCategories}
          onToggleCategory={handleCategoryToggle}
          onResetCategories={resetCategories}
          showCategoryLegend={showCategoryLegend}
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

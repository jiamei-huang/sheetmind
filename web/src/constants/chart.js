/**
 * 图表与分析模式相关常量
 */

/** 支持的图表类型（顺序与默认值相关） */
export const CHART_TYPES = ["bar", "line", "pie"];

/** 图表类型显示标签 */
export const CHART_TYPE_LABELS = {
  bar: "Bar Chart",
  line: "Line Chart",
  pie: "Pie Chart",
};

/** 分析模式标签 */
export const MODE_LABELS = {
  processing: "Data Processing",
  visualization: "Visualization",
  both: "Both (Comprehensive)",
};

/** 可视化模式（含 data 视图） */
export const VISUALIZATION_MODES = ["line", "bar", "pie", "data"];

/** 可视化模式简短标签 */
export const VISUALIZATION_LABELS = {
  bar: "Bar",
  line: "Line",
  pie: "Pie",
  data: "Data",
};

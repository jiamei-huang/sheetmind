/**
 * 提示词分类工具
 */
import { MODE_LABELS } from "../constants/chart";

const PROCESSING_KEYWORDS = [
  "clean",
  "merge",
  "deduplicate",
  "remove",
  "standardize",
  "filter",
  "transform",
  "aggregate",
];

const VISUALIZATION_KEYWORDS = [
  "chart",
  "trend",
  "visual",
  "plot",
  "graph",
  "distribution",
  "heatmap",
  "comparison",
];

/**
 * 根据提示词内容推断分析模式
 * @param {string} [promptText=""] - 用户输入的提示词
 * @returns {{ mode: string, label: string }}
 */
export const classifyPrompt = (promptText = "") => {
  const normalized = promptText.toLowerCase();

  const hasProcessing = PROCESSING_KEYWORDS.some((keyword) => normalized.includes(keyword));
  const hasVisualization = VISUALIZATION_KEYWORDS.some((keyword) =>
    normalized.includes(keyword)
  );

  if (hasProcessing && !hasVisualization) {
    return { mode: "processing", label: MODE_LABELS.processing };
  }
  if (hasVisualization && !hasProcessing) {
    return { mode: "visualization", label: MODE_LABELS.visualization };
  }
  return { mode: "both", label: MODE_LABELS.both };
};

/**
 * 任务相关常量与工厂函数
 */

/** 分页选项 */
export const PAGE_SIZE_OPTIONS = [10, 20, 50];

/** @deprecated 使用 PAGE_SIZE_OPTIONS */
export const pageSizeOptions = PAGE_SIZE_OPTIONS;

/** 默认分页大小 */
export const DEFAULT_PAGE_SIZE = PAGE_SIZE_OPTIONS[0];

/**
 * 创建前端任务对象
 * @param {number} number - 任务序号
 * @param {string} [prompt=""] - 初始提示
 * @param {string|null} [parentTaskId=null] - 父任务 ID
 * @returns {Object} 任务对象
 */
export const createTask = (number, prompt = "", parentTaskId = null) => ({
  id: `task-${number}-${Date.now()}`,
  number,
  prompt,
  title: null,
  status: "draft",
  classification: null,
  mode: null,
  results: [],
  isOpen: true,
  pageSize: DEFAULT_PAGE_SIZE,
  currentPage: 1,
  chartDataPageSize: 25,
  chartDataPage: 1,
  createdAt: Date.now(),
  parentTaskId,
  selectedChartType: "bar",
  isChartDataView: false,
});

/**
 * 任务去重：按 ID 去重，保留最新的任务（按 createdAt 排序）
 * @param {Array} tasks - 任务数组
 * @returns {Array} 去重后的任务数组
 */
export const deduplicateTasks = (tasks) => {
  if (!Array.isArray(tasks) || tasks.length === 0) {
    return tasks;
  }

  const taskMap = new Map();

  tasks.forEach((task) => {
    if (!task || !task.id) {
      console.warn("[deduplicateTasks] 跳过无效任务:", task);
      return;
    }

    const existingTask = taskMap.get(task.id);

    if (
      !existingTask ||
      (task.createdAt && existingTask.createdAt && task.createdAt > existingTask.createdAt)
    ) {
      taskMap.set(task.id, task);
    }
  });

  const deduplicated = Array.from(taskMap.values());

  if (deduplicated.length < tasks.length) {
    console.warn(
      `[deduplicateTasks] ⚠️ 发现重复任务: 原始 ${tasks.length} 个，去重后 ${deduplicated.length} 个`
    );
  }

  return deduplicated;
};

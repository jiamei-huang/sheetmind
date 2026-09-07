/**
 * 校验工具函数
 */

/** UUID 正则：8-4-4-4-12 个十六进制字符 */
const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * 检查是否是有效的后端项目/任务 ID（UUID 格式）
 * 后端项目 ID 示例：550e8400-e29b-41d4-a716-446655440000
 * 前端临时 ID 示例：project-1763378036117
 * @param {string} id - 项目 ID 或任务 ID
 * @returns {boolean}
 */
export const isValidBackendProjectId = (id) => {
  if (!id) return false;
  return UUID_REGEX.test(id);
};

/** 导出正则，供 taskService 等需要抛错信息的场景使用 */
export { UUID_REGEX };

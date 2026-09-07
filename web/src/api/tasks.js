import apiClient from "./client";
import { UUID_REGEX } from "../utils/validation";


const requireProjectId = (projectId) => {
  if (!UUID_REGEX.test(projectId ?? "")) {
    throw new Error("A valid project ID is required");
  }
};


export const getProjectTasks = async (projectId) => {
  requireProjectId(projectId);
  const response = await apiClient.get("/tasks", { params: { projectId } });
  return response.data.tasks ?? [];
};


export const createTask = async (projectId) => {
  requireProjectId(projectId);
  const response = await apiClient.post("/tasks", { projectId });
  return response.data;
};


export const renameTask = async (taskId, name) => {
  await apiClient.patch(`/tasks/${taskId}`, { name });
};


export const deleteTask = async (taskId) => {
  await apiClient.delete(`/tasks/${taskId}`);
};

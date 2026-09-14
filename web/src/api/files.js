import apiClient from "./client";
import { isValidBackendProjectId } from "../utils/validation";


const fileToBase64 = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });


const errorMessage = (error, fallback) =>
  error?.response?.data?.detail ?? error?.message ?? fallback;


export const uploadExcelFile = async ({ file, projectId, projectName }) => {
  if (!file) throw new Error("No file provided");
  const fileData = { fileName: file.name, base64: await fileToBase64(file) };

  try {
    if (isValidBackendProjectId(projectId)) {
      const response = await apiClient.post("/files", {
        projectId,
        files: [fileData],
      });
      return { ...response.data, isExistingProject: true };
    }

    if (!projectName?.trim()) throw new Error("创建新项目时必须提供项目名称");
    const response = await apiClient.post("/projects", {
      projectName: projectName.trim(),
      files: [fileData],
    });
    return response.data;
  } catch (error) {
    throw new Error(errorMessage(error, "文件上传失败，请稍后重试"));
  }
  throw new Error("文件上传失败，请稍后重试");
};


export const getProjectFiles = async (projectId) => {
  const response = await apiClient.get(`/files/project/${projectId}`);
  return response.data;
};


export const previewExcelFile = async ({ projectId, fileId, fileName }) => {
  const response = await apiClient.post("/files/preview", { projectId, fileId, fileName });
  return response.data;
};


export const deleteExcelFile = async ({ projectId, fileId }) => {
  await apiClient.delete(`/files/project/${projectId}/id/${encodeURIComponent(fileId)}`);
};

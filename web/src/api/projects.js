import apiClient from "./client";


export const getProjects = async () => {
  const response = await apiClient.get("/projects");
  return response.data.projects ?? [];
};


export const renameProject = async (projectId, name) => {
  await apiClient.patch(`/projects/${projectId}`, { name });
};


export const deleteProject = async (projectId) => {
  await apiClient.delete(`/projects/${projectId}`);
};

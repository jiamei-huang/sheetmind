import { useState, useCallback, useMemo, useEffect } from "react";
import {
  deleteProject as deleteProjectApi,
  getProjects,
  renameProject as renameProjectApi,
} from "../api/projects";
import { isValidBackendProjectId } from "../utils/validation";

const ACTIVE_PROJECT_KEY = "sheetmind_active_project";

const createProject = (name) => ({
  id: `project-${Date.now()}`,
  name: name || "New Project",
  createdAt: Date.now(),
});

export const useProjectManagement = () => {
  const initialProject = useMemo(() => createProject("Default Project"), []);
  const [projects, setProjects] = useState(() => [initialProject]);
  const [activeProjectId, setActiveProjectId] = useState(initialProject.id);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [isReady, setIsReady] = useState(false);

  // 从后端加载项目列表
  useEffect(() => {
    const loadProjectsFromBackend = async () => {
      setIsLoadingProjects(true);
      try {
        const backendProjects = await getProjects();


        if (backendProjects.length > 0) {
          // 将后端项目转换为前端格式
          const formattedProjects = backendProjects.map((p) => ({
            id: p.projectId, // 使用后端返回的UUID
            name: p.projectName,
            createdAt: p.createdAt ? new Date(p.createdAt).getTime() : Date.now(),
          }));


          setProjects(formattedProjects);
          const remembered = localStorage.getItem(ACTIVE_PROJECT_KEY);
          setActiveProjectId(
            formattedProjects.some((project) => project.id === remembered)
              ? remembered
              : formattedProjects[0].id
          );
        } else {
          setProjects([initialProject]);
          setActiveProjectId(initialProject.id);
        }
      } catch (error) {
        console.error("[useProjectManagement] Error loading projects from backend:", error);
        // 加载失败，保持前端项目列表
      } finally {
        setIsLoadingProjects(false);
        // 标记项目列表加载完成
        setIsReady(true);
      }
    };

    // 应用启动时加载项目列表
    loadProjectsFromBackend();
  }, []); // 只在组件挂载时执行一次

  // 后端项目加载完成后执行
  useEffect(() => {
    if (!isReady) return;

    if (projects.length === 0) {
      // 没有后端项目 → 必须启用 Default Project
      setActiveProjectId(initialProject.id);
    } else {
      // 有后端项目 → 确保 activeProjectId 指向其中一个
      const exists = projects.some(p => p.id === activeProjectId);
      if (!exists) {
        setActiveProjectId(projects[0].id);
      }
    }
  }, [projects, initialProject.id, isReady, activeProjectId]);

  useEffect(() => {
    if (isReady && activeProjectId && isValidBackendProjectId(activeProjectId)) {
      localStorage.setItem(ACTIVE_PROJECT_KEY, activeProjectId);
    }
  }, [activeProjectId, isReady]);

  // 获取唯一的项目名称（自动加序号）
  const getUniqueProjectName = useCallback((baseName, existingNames) => {
    let name = baseName;
    let counter = 1;
    while (existingNames.includes(name)) {
      name = `${baseName} (${counter})`;
      counter++;
    }
    return name;
  }, []);

  const createNewProject = useCallback(() => {
    if (!isReady) return activeProjectId;
    // 确保项目名称唯一（包括前端和后端项目）
    const existingNames = projects.map(p => p.name);
    const baseName = `Project ${projects.length + 1}`;
    const uniqueName = getUniqueProjectName(baseName, existingNames);

    const newProject = createProject(uniqueName);
    setProjects((prev) => [...prev, newProject]);
    setActiveProjectId(newProject.id);
    return newProject.id;
  }, [projects, getUniqueProjectName, isReady, activeProjectId]);

  const selectProject = useCallback((projectId) => {
    setActiveProjectId(projectId);
  }, []);

  const deleteProject = useCallback(async (projectId) => {
    if (isValidBackendProjectId(projectId)) {
      await deleteProjectApi(projectId);
    }
    // 清理该项目的任务数据
    try {
      localStorage.removeItem(`tasks_${projectId}`);
    } catch (e) {
      console.error("Failed to remove project tasks from localStorage", e);
    }

    setProjects((prev) => {
      const remaining = prev.filter((p) => p.id !== projectId);
      if (remaining.length === 0) {
        const defaultProject = createProject("Default Project");
        setActiveProjectId(defaultProject.id);
        return [defaultProject];
      }
      if (activeProjectId === projectId) {
        setActiveProjectId(remaining[0].id);
      }
      return remaining;
    });
  }, [activeProjectId]);

  const renameProject = useCallback(async (projectId, newName) => {
    if (isValidBackendProjectId(projectId)) {
      await renameProjectApi(projectId, newName.trim());
    }
    setProjects((prev) =>
      prev.map((p) => (p.id === projectId ? { ...p, name: newName.trim() } : p))
    );
  }, []);

  const activeProject = useMemo(
    () => projects.find((p) => p.id === activeProjectId) || projects[0],
    [projects, activeProjectId]
  );

  return {
    projects,
    setProjects,
    activeProjectId,
    setActiveProjectId,
    activeProject,
    createNewProject,
    selectProject,
    deleteProject,
    renameProject,
    isLoadingProjects,
    isReady,
  };
};

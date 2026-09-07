import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import {
  deleteProject as deleteProjectApi,
  getProjects,
  renameProject as renameProjectApi,
} from "../api/projects";
import { isValidBackendProjectId } from "../utils/validation";

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
  const projectsReadyRef = useRef(false);

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


          // 更新项目列表
          setProjects((prev) => {
            // 合并后端项目和前端临时项目（避免重复）
            const existingIds = new Set(prev.map(p => p.id));
            const newBackendProjects = formattedProjects.filter(p => !existingIds.has(p.id));
            const frontendProjects = prev.filter(p => !p.id.match(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i));

            const merged = [...frontendProjects, ...newBackendProjects];

            return merged.length > 0 ? merged : [initialProject];
          });

          // 如果当前活动项目不在后端项目中，设置为第一个后端项目
          setActiveProjectId((currentActiveId) => {
            const backendProjectIds = formattedProjects.map(p => p.id);
            if (backendProjectIds.length > 0 && !backendProjectIds.includes(currentActiveId)) {
              return formattedProjects[0].id;
            }
            return currentActiveId;
          });
        }
      } catch (error) {
        console.error("[useProjectManagement] Error loading projects from backend:", error);
        // 加载失败，保持前端项目列表
      } finally {
        setIsLoadingProjects(false);
        // 标记项目列表加载完成
        projectsReadyRef.current = true;
      }
    };

    // 应用启动时加载项目列表
    loadProjectsFromBackend();
  }, []); // 只在组件挂载时执行一次

  // 后端项目加载完成后执行
  useEffect(() => {
    if (!projectsReadyRef.current) return;

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
  }, [projects, initialProject.id]);

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
    // 确保项目名称唯一（包括前端和后端项目）
    const existingNames = projects.map(p => p.name);
    const baseName = `Project ${projects.length + 1}`;
    const uniqueName = getUniqueProjectName(baseName, existingNames);

    const newProject = createProject(uniqueName);
    setProjects((prev) => [...prev, newProject]);
    setActiveProjectId(newProject.id);
    return newProject.id;
  }, [projects, getUniqueProjectName]);

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
    projectsReadyRef, // 导出给 useTaskManagement 使用
  };
};

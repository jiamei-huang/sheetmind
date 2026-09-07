import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isValidBackendProjectId } from "../utils/validation";
import { getProjectFiles } from "../api/files";

const STORAGE_KEY_PREFIX = "excel_";

/**
 * 格式化文件大小
 */
const formatFileSize = (bytes) => {
  if (!bytes || bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
};

/**
 * 文件管理 Hook
 * 管理按项目分组的文件列表、localStorage 持久化、后端加载
 */
export const useFileManagement = (activeProjectId) => {
  const filesByProjectRef = useRef({});
  const prevProjectIdRef = useRef(null);
  const isInitialMountRef = useRef(true);
  const currentLoadTokenRef = useRef(null);

  const [updateTrigger, setUpdateTrigger] = useState(0);
  const forceUpdate = useCallback(() => {
    setUpdateTrigger((prev) => prev + 1);
  }, []);

  const uploadedFiles = useMemo(() => {
    if (!activeProjectId) return [];
    return filesByProjectRef.current[activeProjectId] || [];
  }, [activeProjectId, updateTrigger]);

  const saveFilesToLocalStorage = useCallback((projectId, files) => {
    if (!projectId) return;
    try {
      const key = `${STORAGE_KEY_PREFIX}${projectId}`;
      const filesToSave = (files || [])
        .map((file) => ({
          id: file.id,
          fileId: file.fileId,
          name: file.name,
          size: file.size,
          uploadedAt: file.uploadedAt,
          sheets: file.sheets || [],
          selectedSheets: file.selectedSheets || [],
          isExpanded: file.isExpanded || false,
          projectId: file.projectId || projectId,
        }))
        .filter((f) => (f.projectId || projectId) === projectId);
      localStorage.setItem(key, JSON.stringify(filesToSave));
    } catch (e) {
      console.error("[useFileManagement] Failed to save files:", e);
    }
  }, []);

  const loadFilesFromLocalStorage = useCallback((projectId) => {
    if (!projectId || !isValidBackendProjectId(projectId)) return [];
    try {
      const key = `${STORAGE_KEY_PREFIX}${projectId}`;
      const stored = localStorage.getItem(key);
      if (stored) {
        return JSON.parse(stored).map((f) => ({ ...f, projectId }));
      }
    } catch (e) {
      console.error("[useFileManagement] Failed to load files:", e);
    }
    return [];
  }, []);

  const addFileToProject = useCallback(
    (projectId, file) => {
      if (!projectId || !file) return;
      const fileProjectId = file.projectId || projectId;
      if (fileProjectId !== projectId) return;

      const old = filesByProjectRef.current[projectId] || [];
      if (old.some((f) => f.id === file.id)) return;

      filesByProjectRef.current[projectId] = [...old, file];
      saveFilesToLocalStorage(projectId, filesByProjectRef.current[projectId]);
      forceUpdate();
    },
    [saveFilesToLocalStorage, forceUpdate]
  );

  const removeFileFromProject = useCallback(
    (projectId, fileId) => {
      if (!projectId || !fileId) return;
      const old = filesByProjectRef.current[projectId] || [];
      filesByProjectRef.current[projectId] = old.filter((f) => f.id !== fileId);
      saveFilesToLocalStorage(projectId, filesByProjectRef.current[projectId]);
      forceUpdate();
    },
    [saveFilesToLocalStorage, forceUpdate]
  );

  const handleFilesChange = useCallback(
    (files, targetProjectId = null) => {
      const projectId = targetProjectId || activeProjectId;
      if (!projectId) return;

      const normalizedFiles = (files || []).map((f) => ({ ...f, projectId }));
      filesByProjectRef.current[projectId] = normalizedFiles;
      localStorage.setItem(
        `${STORAGE_KEY_PREFIX}${projectId}`,
        JSON.stringify(normalizedFiles)
      );
      forceUpdate();
    },
    [activeProjectId, forceUpdate]
  );

  const loadFilesForProject = useCallback(
    async (projectId, token = null) => {
      if (!projectId) return;

      if (!token) {
        token = `${Date.now()}-${Math.random()}`;
        currentLoadTokenRef.current = token;
      }

      if (isInitialMountRef.current) {
        filesByProjectRef.current[projectId] = [];
        forceUpdate();
        return;
      }

      if (!isValidBackendProjectId(projectId)) {
        filesByProjectRef.current[projectId] = [];
        forceUpdate();
        return;
      }

      const key = `${STORAGE_KEY_PREFIX}${projectId}`;
      const stored = localStorage.getItem(key);
      const fromStorage = stored ? JSON.parse(stored) : [];

      if (fromStorage.length > 0) {
        const valid = fromStorage.map((f) => ({ ...f, projectId }));
        if (currentLoadTokenRef.current !== token) return;
        filesByProjectRef.current[projectId] = valid;
        localStorage.setItem(key, JSON.stringify(valid));
        forceUpdate();
        return;
      }

      try {
        const response = await getProjectFiles(projectId);
        if (currentLoadTokenRef.current !== token) return;

        const formattedFiles = (response.files || []).map((file, index) => ({
          id: file.fileId || file.fileName || `file-${index}`,
          fileId: file.fileId || file.fileName,
          name: file.fileName,
          size: file.fileSize ? formatFileSize(file.fileSize) : "Unknown",
          uploadedAt: file.createdAt
            ? new Date(file.createdAt).toLocaleString()
            : new Date().toLocaleString(),
          sheets: file.sheets || [],
          selectedSheets: file.sheets?.length === 1 ? [file.sheets[0]] : [],
          isExpanded: false,
          projectId,
        }));

        if (currentLoadTokenRef.current !== token) return;
        filesByProjectRef.current[projectId] = formattedFiles;
        localStorage.setItem(key, JSON.stringify(formattedFiles));
        forceUpdate();
      } catch (error) {
        console.error("[useFileManagement] Error loading files:", error);
        if (currentLoadTokenRef.current !== token) return;
        filesByProjectRef.current[projectId] = [];
        forceUpdate();
      }
    },
    [forceUpdate]
  );

  useEffect(() => {
    if (!isInitialMountRef.current) return;
    isInitialMountRef.current = false;
  }, []);

  useEffect(() => {
    if (!activeProjectId || activeProjectId === prevProjectIdRef.current) return;

    prevProjectIdRef.current = activeProjectId;

    if (isInitialMountRef.current) {
      filesByProjectRef.current[activeProjectId] = [];
      forceUpdate();
      return;
    }

    const token = `${Date.now()}-${Math.random()}`;
    currentLoadTokenRef.current = token;
    loadFilesForProject(activeProjectId, token);
  }, [activeProjectId, loadFilesForProject, forceUpdate]);

  /** 同步 prevProjectId，用于 onProjectCreated 后避免重复加载 */
  const setPrevProjectId = useCallback((id) => {
    prevProjectIdRef.current = id;
  }, []);

  return {
    filesByProjectRef,
    uploadedFiles,
    addFileToProject,
    removeFileFromProject,
    handleFilesChange,
    loadFilesForProject,
    saveFilesToLocalStorage,
    loadFilesFromLocalStorage,
    forceUpdate,
    setPrevProjectId,
  };
};

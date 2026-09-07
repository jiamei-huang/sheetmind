import { useCallback, useEffect, useMemo, useState } from "react";

import {
  createTask as createTaskApi,
  deleteTask as deleteTaskApi,
  getProjectTasks,
  renameTask as renameTaskApi,
} from "../api/tasks";
import { createTask as createLocalTask, deduplicateTasks, PAGE_SIZE_OPTIONS } from "../constants/task";
import { isValidBackendProjectId } from "../utils/validation";


const lastTaskKey = (projectId) => `lastActiveTask_${projectId}`;
const localTasksKey = (projectId) => `tasks_${projectId}`;


const toFrontendTask = (task, projectId) => ({
  id: task.taskId,
  taskId: task.taskId,
  number: task.taskNumber,
  projectId: task.projectId ?? projectId,
  prompt: "",
  title: task.title ?? null,
  status: task.status ?? "draft",
  classification: null,
  mode: null,
  results: [],
  isOpen: false,
  pageSize: PAGE_SIZE_OPTIONS[0],
  currentPage: 1,
  chartDataPageSize: 25,
  chartDataPage: 1,
  createdAt: task.createdAt ? new Date(task.createdAt).getTime() : Date.now(),
  parentTaskId: null,
  selectedChartType: "bar",
  isChartDataView: false,
});


const newLocalTask = (number, projectId, prompt = "", parentTaskId = null) => ({
  ...createLocalTask(number, prompt, parentTaskId),
  projectId,
});


const readLocalTasks = (projectId) => {
  try {
    const parsed = JSON.parse(localStorage.getItem(localTasksKey(projectId)) ?? "[]");
    return deduplicateTasks(parsed);
  } catch {
    return [];
  }
};


export const useTaskManagement = (activeProjectId, projectsReadyRef, suppressTaskReloadRef) => {
  const [tasksByProject, setTasksByProject] = useState({});
  const [activeTaskId, setActiveTaskIdState] = useState(null);
  const [isLoadingTasks, setIsLoadingTasks] = useState(false);

  const tasks = useMemo(
    () => (activeProjectId ? tasksByProject[activeProjectId] ?? [] : []),
    [activeProjectId, tasksByProject]
  );

  const rememberActiveTask = useCallback((taskId, projectId = activeProjectId) => {
    setActiveTaskIdState(taskId);
    if (projectId && taskId) localStorage.setItem(lastTaskKey(projectId), taskId);
  }, [activeProjectId]);

  const setTasks = useCallback((updater) => {
    if (!activeProjectId) return;
    setTasksByProject((current) => {
      const previous = current[activeProjectId] ?? [];
      const next = deduplicateTasks(
        typeof updater === "function" ? updater(previous) : updater
      );
      if (!isValidBackendProjectId(activeProjectId)) {
        localStorage.setItem(localTasksKey(activeProjectId), JSON.stringify(next));
      }
      return { ...current, [activeProjectId]: next };
    });
  }, [activeProjectId]);

  const setActiveTaskId = useCallback(
    (value) => {
      const next = typeof value === "function" ? value(activeTaskId) : value;
      rememberActiveTask(next);
    },
    [activeTaskId, rememberActiveTask]
  );

  useEffect(() => {
    if (!activeProjectId || suppressTaskReloadRef?.current) return;

    if (!isValidBackendProjectId(activeProjectId)) {
      const stored = readLocalTasks(activeProjectId);
      const localTasks = stored.length ? stored : [newLocalTask(1, activeProjectId)];
      setTasksByProject((current) => ({ ...current, [activeProjectId]: localTasks }));
      const remembered = localStorage.getItem(lastTaskKey(activeProjectId));
      rememberActiveTask(
        localTasks.some((task) => task.id === remembered) ? remembered : localTasks[0].id,
        activeProjectId
      );
      return;
    }

    if (projectsReadyRef && !projectsReadyRef.current) return;
    let cancelled = false;
    setIsLoadingTasks(true);
    getProjectTasks(activeProjectId)
      .then(async (items) => {
        const source = items.length ? items : [await createTaskApi(activeProjectId)];
        if (cancelled) return;
        const previous = tasksByProject[activeProjectId] ?? [];
        const previousById = new Map(previous.map((task) => [task.id, task]));
        const loaded = source.map((item) => {
          const task = toFrontendTask(item, activeProjectId);
          return { ...task, ...(previousById.get(task.id) ?? {}), projectId: activeProjectId };
        });
        setTasksByProject((current) => ({ ...current, [activeProjectId]: loaded }));
        const remembered = localStorage.getItem(lastTaskKey(activeProjectId));
        rememberActiveTask(
          loaded.some((task) => task.id === remembered) ? remembered : loaded.at(-1).id,
          activeProjectId
        );
      })
      .catch(() => {
        if (!cancelled) setTasksByProject((current) => ({ ...current, [activeProjectId]: [] }));
      })
      .finally(() => {
        if (!cancelled) setIsLoadingTasks(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeProjectId, projectsReadyRef, rememberActiveTask, suppressTaskReloadRef]);

  const updateTask = useCallback((taskId, updater) => {
    setTasks((current) =>
      current.map((task) =>
        task.id === taskId ? { ...task, ...updater(task) } : task
      )
    );
  }, [setTasks]);

  const selectTask = useCallback((taskId) => {
    rememberActiveTask(taskId);
    setTasks((current) =>
      current.map((task) => ({ ...task, isOpen: task.id === taskId }))
    );
  }, [rememberActiveTask, setTasks]);

  const createNewTask = useCallback(async (sourceTaskId) => {
    if (!activeProjectId) return;
    const task = isValidBackendProjectId(activeProjectId)
      ? toFrontendTask(await createTaskApi(activeProjectId), activeProjectId)
      : newLocalTask(tasks.length + 1, activeProjectId, "", sourceTaskId);
    setTasks((current) => [
      ...current.map((item) => ({ ...item, isOpen: false })),
      { ...task, isOpen: true },
    ]);
    rememberActiveTask(task.id);
  }, [activeProjectId, rememberActiveTask, setTasks, tasks.length]);

  const deleteTask = useCallback(async (taskId) => {
    if (!activeProjectId) return;
    if (isValidBackendProjectId(taskId)) await deleteTaskApi(taskId);

    let remaining = tasks.filter((task) => task.id !== taskId);
    if (!remaining.length) {
      remaining = [
        isValidBackendProjectId(activeProjectId)
          ? toFrontendTask(await createTaskApi(activeProjectId), activeProjectId)
          : newLocalTask(1, activeProjectId),
      ];
    }
    remaining = remaining.map((task, index) => ({ ...task, number: index + 1 }));
    setTasks(remaining);
    if (activeTaskId === taskId) rememberActiveTask(remaining[0].id);
  }, [activeProjectId, activeTaskId, rememberActiveTask, setTasks, tasks]);

  const renameTask = useCallback(async (taskId, title) => {
    const name = title.trim();
    if (isValidBackendProjectId(taskId)) await renameTaskApi(taskId, name);
    updateTask(taskId, () => ({ title: name }));
  }, [updateTask]);

  const migrateTempToReal = useCallback(async (tempId, realId) => {
    const temporary = tasksByProject[tempId] ?? [];
    const remoteItems = await getProjectTasks(realId);
    const remote = remoteItems.map((item) => toFrontendTask(item, realId));
    while (remote.length < temporary.length) {
      remote.push(toFrontendTask(await createTaskApi(realId), realId));
    }
    const merged = remote.map((task, index) => ({
      ...task,
      ...(temporary[index] ?? {}),
      id: task.id,
      taskId: task.id,
      projectId: realId,
    }));

    setTasksByProject((current) => {
      const next = { ...current, [realId]: merged };
      delete next[tempId];
      return next;
    });
    localStorage.removeItem(localTasksKey(tempId));
    localStorage.removeItem(lastTaskKey(tempId));
    const selectedActiveAfter = merged[0]?.id ?? null;
    if (selectedActiveAfter) rememberActiveTask(selectedActiveAfter, realId);
    return { mergedTasks: merged, selectedActiveAfter, tempTaskIdMap: new Map() };
  }, [rememberActiveTask, tasksByProject]);

  return {
    tasks,
    setTasks,
    activeTaskId,
    setActiveTaskId,
    isLoadingTasks,
    updateTask,
    createNewTask,
    selectTask,
    deleteTask,
    renameTask,
    migrateTempToReal,
  };
};

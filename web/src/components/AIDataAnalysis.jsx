import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import TaskContentPanel from "./AIDataAnalysis/TaskContentPanel";
import {
  analyzeStream,
  buildSelectedFileScope,
  downloadArtifactExcel,
  toAnalysisErrorMessage,
} from "../api/analysis";
import { createTask as createTaskApi } from "../api/tasks";
import { toAnalysisViewModel } from "../api/resultBlocks";
import { isValidBackendProjectId } from "../utils/validation";
import { createTask, PAGE_SIZE_OPTIONS } from "../constants/task";
import {
  CHART_TYPES,
  CHART_TYPE_LABELS,
  DEFAULT_CHART_DISPLAY_LIMIT,
} from "../constants/chart";
import {
  downloadChartDataAsExcel,
  downloadTableDataAsExcel,
} from "../utils/chartExcelExport";
import { MAX_ANALYSIS_QUERY_LENGTH } from "../constants/analysis";

function AIDataAnalysis({
  uploadedFile,
  uploadedFiles = [],
  uploadedFilesCount = 0,
  selectedSheetsTotal = 0,
  hasUploadedFiles = false,
  onShowToast,
  onShowErrorModal,
  taskManagement,
  activeProjectId, // 从App.jsx传入的项目ID
}) {
  const { tasks, setTasks, activeTaskId, setActiveTaskId, updateTask } = taskManagement || {};
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [progressMsg, setProgressMsg] = useState("");
  const [progressSteps, setProgressSteps] = useState([]);
  const [isConfirmDiscardOpen, setIsConfirmDiscardOpen] = useState(false);
  const [confirmDiscardTaskId, setConfirmDiscardTaskId] = useState(null);
  const [exportMenuKey, setExportMenuKey] = useState(null);
  const chartContainerRefs = useRef({});
  const [isRenameModalOpen, setIsRenameModalOpen] = useState(false);
  const [renameTaskId, setRenameTaskId] = useState(null);
  const [renameTaskTitle, setRenameTaskTitle] = useState("");
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [deleteTaskId, setDeleteTaskId] = useState(null);

  useEffect(() => {
    if (!exportMenuKey) {
      return undefined;
    }

    const handleClickOutside = () => {
      setExportMenuKey(null);
    };

    document.addEventListener("click", handleClickOutside);
    return () => {
      document.removeEventListener("click", handleClickOutside);
    };
  }, [exportMenuKey]);

  // 创建别名变量以保持代码一致性
  const effectiveTasks = tasks || [];
  const effectiveSetTasks = setTasks || (() => {});
  const effectiveActiveTaskId = activeTaskId || null;
  const effectiveSetActiveTaskId = setActiveTaskId || (() => {});
  const effectiveUpdateTask = updateTask || (() => {});

  const activeTask = useMemo(() => {
    // 强制绑定：只返回与 activeTaskId 匹配的任务
    if (!effectiveActiveTaskId) {
      const firstTask = effectiveTasks[0] || null;
      return firstTask;
    }
    const task = effectiveTasks.find((task) => task.id === effectiveActiveTaskId);
    return task || null;
  }, [effectiveTasks, effectiveActiveTaskId]);

  // 文件选择逻辑：优先使用任务保存的选择，否则使用全局的 uploadedFile.selectedSheets
  const selectedSheets = useMemo(() => {
    // 优先级1：如果当前任务有保存的文件选择，使用任务的
    const taskFileSelection = activeTask?.selectedFiles?.selectedSheets;
    if (taskFileSelection && Array.isArray(taskFileSelection) && taskFileSelection.length > 0) {
      return taskFileSelection;
    }

    // 优先级2：如果任务已经执行过分析，且有保存的文件选择，使用保存的选择
    const hasTaskUsedFiles = activeTask?.results?.length > 0;
    if (hasTaskUsedFiles && taskFileSelection) {
      return taskFileSelection;
    }

    // 优先级3：使用全局的 uploadedFile.selectedSheets（适用于新任务和已完成任务）
    // 这样新任务可以立即使用用户已选择的 sheet
    if (uploadedFile?.selectedSheets && Array.isArray(uploadedFile.selectedSheets) && uploadedFile.selectedSheets.length > 0) {
      return uploadedFile.selectedSheets;
    }

    // 没有找到任何文件选择
    return [];
  }, [activeTask, uploadedFile]);

  const selectedFileScope = useMemo(() => {
    const completeScope = buildSelectedFileScope(uploadedFiles);
    if (completeScope.length) {
      return completeScope;
    }
    return buildSelectedFileScope([
      uploadedFile
        ? { ...uploadedFile, selectedSheets }
        : null,
    ]);
  }, [selectedSheets, uploadedFile, uploadedFiles]);

  // updateTask 现在从 taskManagement 获取或使用本地版本

  const handleTaskPromptChange = useCallback((taskId, value) => {
    effectiveSetTasks((prev) =>
      prev.map((task) =>
        task.id === taskId
          ? {
              ...task,
              prompt: value.slice(0, MAX_ANALYSIS_QUERY_LENGTH),
              status: value.trim()
                ? "draft"
                : task.results?.length
                  ? "completed"
                  : "draft",
              classification: task.status === "completed" ? task.classification : null,
            }
          : task
      )
    );
  }, [effectiveSetTasks]);

  const handleTaskPageSizeChange = useCallback((taskId, size) => {
    effectiveUpdateTask(taskId, () => ({ pageSize: size, currentPage: 1 }));
  }, [effectiveUpdateTask]);

  const handleTaskPageChange = useCallback((taskId, page) => {
    effectiveUpdateTask(taskId, () => ({ currentPage: page }));
  }, [effectiveUpdateTask]);

  // Chart Data 分页相关回调
  const handleTaskChartDataPageSizeChange = useCallback((taskId, size) => {
    effectiveUpdateTask(taskId, () => ({ chartDataPageSize: size, chartDataPage: 1 }));
  }, [effectiveUpdateTask]);

  const handleTaskChartDataPageChange = useCallback((taskId, page) => {
    effectiveUpdateTask(taskId, () => ({ chartDataPage: page }));
  }, [effectiveUpdateTask]);

  const handleStepPage = useCallback((taskId, direction) => {
    effectiveUpdateTask(taskId, (task) => {
      const rows = task.results?.[task.results.length - 1]?.preview?.rows ?? [];
      const size = task.pageSize ?? PAGE_SIZE_OPTIONS[0];
      const totalPages = Math.max(1, Math.ceil(rows.length / size));
      const nextPage = Math.min(
        Math.max(1, (task.currentPage ?? 1) + direction),
        totalPages
      );
      return { currentPage: nextPage };
    });
  }, [effectiveUpdateTask]);


  const handleExportPreview = useCallback(async (taskId, resultIndex, previewIndex) => {
    const targetTask = effectiveTasks.find((task) => task.id === taskId);
    const results = targetTask?.results || [];
    const resolvedResultIndex = resultIndex ?? results.length - 1;
    const result = results[resolvedResultIndex] ?? null;
    const preview = result?.previews?.[previewIndex ?? targetTask?.activePreviewIndex ?? 0]
      ?? result?.preview;
    if (!preview?.columns?.length) {
      onShowToast?.({ title: "Export failed", message: "No table data is available to export.", type: "error" });
      return;
    }

    try {
      if (preview.artifactId) {
        await downloadArtifactExcel(preview.artifactId, taskId);
      } else {
        await downloadTableDataAsExcel(
          preview,
          `analysis-data-${taskId}-${resolvedResultIndex + 1}`
        );
      }
      onShowToast?.({ title: "Export complete", message: "The calculated result was downloaded as Excel.", type: "success", autoClose: true });
    } catch (err) {
      console.error("[Export Table]", err);
      onShowToast?.({ title: "Export failed", message: err?.message || "Something went wrong while exporting data.", type: "error" });
    }
  }, [onShowToast, effectiveTasks]);

  const handleTaskVisualizationModeChange = useCallback(
    (taskId, mode) => {
      if (mode === "data") {
        effectiveUpdateTask(taskId, () => ({ isChartDataView: true }));
        return;
      }

      if (!CHART_TYPES.includes(mode)) {
        return;
      }

      effectiveUpdateTask(taskId, () => ({ selectedChartType: mode, isChartDataView: false }));
    },
    [effectiveUpdateTask]
  );

  const handleExportChartImage = useCallback(
    async (taskId, resultIndex, chartIndex = 0) => {
      const targetTask = effectiveTasks.find((task) => task.id === taskId);
      const results = targetTask?.results || [];
      const idx = resultIndex ?? results.length - 1;
      const result = results[idx] ?? null;
      if (!result?.chartData) {
        onShowToast?.({ title: "Export failed", message: "No chart data is available to export.", type: "error" });
        return;
      }
      const chartData = result.chartDatas?.[chartIndex] ?? result.chartData;

      try {
        onShowToast?.({ title: "Exporting", message: "Generating chart image...", type: "info", autoClose: true });

        const container = chartContainerRefs.current[`${taskId}-${idx}`];
        if (!container) {
          onShowToast?.({ title: "Export failed", message: "Could not locate the chart area. Please try again.", type: "error" });
          return;
        }
        const { default: html2canvas } = await import("html2canvas");
        const canvas = await html2canvas(container, {
          useCORS: true,
          scale: 2,
          backgroundColor: "#ffffff",
          logging: false,
        });
        const dataUrl = canvas.toDataURL("image/png");
        const link = document.createElement("a");
        link.href = dataUrl;
        link.download = `chart-${taskId}-${idx}.png`;
        link.click();
        onShowToast?.({ title: "Export complete", message: "The chart image was downloaded.", type: "success", autoClose: true });
      } catch (err) {
        console.error("[Export Image]", err);
        onShowToast?.({ title: "Export failed", message: err?.message || "Something went wrong while generating the image.", type: "error" });
      }
    },
    [onShowToast, effectiveTasks]
  );

  const handleExportChartData = useCallback(
    async (taskId, resultIndex, chartIndex = 0) => {
      const targetTask = effectiveTasks.find((task) => task.id === taskId);
      const results = targetTask?.results || [];
      const idx = resultIndex ?? results.length - 1;
      const result = results[idx] ?? null;
      const chartData = result?.chartDatas?.[chartIndex]
        ?? result?.chartData;
      if (!chartData?.labels?.length || !chartData.series?.length) {
        onShowToast?.({ title: "Export failed", message: "No chart data is available to export.", type: "error" });
        return;
      }

      try {
        await downloadChartDataAsExcel(chartData, `chart-data-${taskId}-${idx}`);
        onShowToast?.({ title: "Export complete", message: "The chart data was downloaded as Excel.", type: "success", autoClose: true });
      } catch (err) {
        console.error("[Export Data]", err);
        onShowToast?.({ title: "Export failed", message: err?.message || "Something went wrong while exporting data.", type: "error" });
      }
    },
    [onShowToast, effectiveTasks]
  );

  const toggleExportMenuForTask = useCallback((menuKey) => {
    setExportMenuKey((prev) => (prev === menuKey ? null : menuKey));
  }, []);

  const handleAnalyzeTask = useCallback(async (taskId) => {
    // 首先尝试使用传入的taskId查找任务
    let task = effectiveTasks.find((item) => item.id === taskId);

    // 如果找不到任务，可能是taskId已经更新为后端UUID，尝试使用activeTaskId
    if (!task && effectiveActiveTaskId) {
      task = effectiveTasks.find((item) => item.id === effectiveActiveTaskId);
    }

    // 如果还是找不到，尝试使用第一个任务（fallback）
    if (!task && effectiveTasks.length > 0) {
      task = effectiveTasks[0];
      // 同时更新activeTaskId
      if (task) {
        effectiveSetActiveTaskId(task.id);
      }
    }

    if (!task) {
      console.error("[AIDataAnalysis] No task found, cannot proceed with analysis");
      onShowErrorModal?.({
        message: "No task was found. Create a task first.",
        title: "Task error",
      });
      return;
    }

    // 如果找到的任务ID与传入的taskId不同，更新taskId为实际的任务ID
    if (task.id !== taskId) {
      taskId = task.id;
    }

    const ready = Boolean(uploadedFile?.fileId && selectedSheets.length > 0);
    if (!ready) {
      // 轻量提示：使用Toast（自动关闭）
      onShowToast?.({
        title: "Missing information",
        message: "Upload an Excel file and select at least one sheet first.",
        type: "info",
        autoClose: true,
      });
      return;
    }

    const trimmedPrompt = task.prompt.trim();
    if (!trimmedPrompt) {
      // 轻量提示：使用Toast（自动关闭）
      onShowToast?.({
        message: "Enter what you want to analyze or clean.",
        type: "info",
        autoClose: true,
      });
      return;
    }

    effectiveSetTasks((prev) =>
      prev.map((item) =>
        item.id === taskId
          ? {
              ...item,
              prompt: trimmedPrompt,
              status: "running",
              classification: "AI Analysis",
              mode: "auto",
              analysisError: null,
            }
          : item
      )
    );
    setIsAnalyzing(true);
    setProgressMsg("Preparing analysis...");
    setProgressSteps([]);

    let currentTaskId = taskId;

    try {
      // 获取实际的 taskId
      // 如果任务是从后端加载的，task.id 就是后端的 taskId（UUID格式）
      // 如果任务是前端临时创建的，task.id 是前端临时ID（如 task-1-xxx），需要先在后端创建任务
      let actualTaskId = task.id;

      // 如果任务ID不是后端UUID格式，说明是前端临时任务，需要先在后端创建
      if (!isValidBackendProjectId(actualTaskId)) {

        // 获取当前项目ID（从props传入）
        let currentProjectId = activeProjectId;

        // 如果activeProjectId不是后端UUID，尝试从uploadedFile中获取projectId
        if (!currentProjectId || !isValidBackendProjectId(currentProjectId)) {
          if (uploadedFile?.projectId) {
            currentProjectId = uploadedFile.projectId;
          }
        }

        if (!currentProjectId) {
          onShowErrorModal?.({
            message: "Project ID is missing. Make sure an Excel file has been uploaded.",
            title: "Task error",
          });
          return;
        }


        if (!isValidBackendProjectId(currentProjectId)) {
          console.error("[AIDataAnalysis] ProjectId is not backend UUID:", currentProjectId);
          console.error("[AIDataAnalysis] uploadedFile:", uploadedFile);
          console.error("[AIDataAnalysis] activeProjectId from props:", activeProjectId);
          onShowErrorModal?.({
            message: `The current project is not ready for analysis (project ID: ${currentProjectId}). Upload an Excel file to create a project. If you already uploaded one, refresh the page.`,
            title: "Task error",
          });
          return;
        }

        // 调用后端API创建任务
        try {
          const createResponse = await createTaskApi(currentProjectId);
          actualTaskId = createResponse.taskId;
          currentTaskId = actualTaskId;


          // 更新任务对象，将前端临时ID替换为后端taskId
          effectiveSetTasks((prev) =>
            prev.map((item) =>
              item.id === taskId
                ? {
                    ...item,
                    id: actualTaskId, // 更新为后端taskId
                    taskId: actualTaskId, // 也保存taskId字段
                  }
                : item
            )
          );

          // 重要：同时更新activeTaskId，确保activeTask能正确找到任务
          effectiveSetActiveTaskId(actualTaskId);
        } catch (createError) {
          console.error("[AIDataAnalysis] Error creating backend task:", createError);
          onShowErrorModal?.({
            message: `Could not create the task: ${createError?.response?.data?.detail || createError?.message || "Unknown error"}`,
            title: "Task creation failed",
          });
          return;
        }
      }

      if (!actualTaskId) {
        // 重要错误：使用弹窗（手动关闭）
        onShowErrorModal?.({
          message: "Task ID is missing. Create a task first.",
          title: "Task error",
        });
        return;
      }


      const pushProgressEvent = (event) => {
        const message = event?.message || "";
        if (!message) {
          return;
        }
        setProgressMsg(message);
        setProgressSteps((prev) => {
          const last = prev[prev.length - 1];
          if (last?.event === event.event && last?.message === message) {
            return prev;
          }
          return [
            ...prev,
            {
              event: event.event || "progress",
              message,
              at: Date.now(),
            },
          ].slice(-8);
        });
      };

      const response = await analyzeStream({
        taskId: actualTaskId,
        query: trimmedPrompt,
        selectedFiles: selectedFileScope,
        onEvent: pushProgressEvent,
      });

      const newResult = toAnalysisViewModel(response, trimmedPrompt);

      effectiveSetTasks((prev) => {
        const defaultChartType = newResult.chartData && !newResult.chartData.type
          ? CHART_TYPES.includes(newResult.chartData.defaultType)
            ? newResult.chartData.defaultType
            : CHART_TYPES[0]
          : CHART_TYPES[0];

        // 使用actualTaskId来更新任务结果（如果创建了后端任务，taskId可能已经更新为actualTaskId）
        const targetTaskId = actualTaskId || taskId;

        return prev.map((item) => {
          if (item.id === targetTaskId) {
            return {
              ...item,
              status: newResult.status === "failed" ? "failed" : "completed",
              results: [...(item.results || []), newResult],
              classification: newResult.classification,
              mode: newResult.mode,
              prompt: "", // 清空prompt以便继续输入
              analysisError: null,
              isOpen: true,
              pageSize: item.pageSize ?? PAGE_SIZE_OPTIONS[0],
              currentPage: 1,
              selectedChartType: defaultChartType,
              isChartDataView: false,
              chartDisplayLimit: DEFAULT_CHART_DISPLAY_LIMIT,
            };
          }
          return item;
        });
      });

      // 轻量提示：使用Toast（自动关闭）
      onShowToast?.({
        title: "Analysis complete",
        message: "The preview is ready.",
        type: "success",
        autoClose: true,
      });
    } catch (error) {
      const message = toAnalysisErrorMessage(error);
      effectiveSetTasks((prev) =>
        prev.map((item) =>
          item.id === taskId
          || item.id === currentTaskId
            ? {
                ...item,
                status: "failed",
                analysisError: message,
              }
            : item
        )
      );

      // 重要错误：使用弹窗（手动关闭）
      onShowErrorModal?.({
        message,
        title: "Analysis incomplete",
      });
    } finally {
      setIsAnalyzing(false);
      setProgressMsg("");
      setProgressSteps([]);
    }
  }, [
    activeProjectId,
    effectiveActiveTaskId,
    effectiveSetActiveTaskId,
    effectiveSetTasks,
    effectiveTasks,
    onShowErrorModal,
    onShowToast,
    selectedSheets,
    selectedFileScope,
    uploadedFile,
  ]);

  const handleCreateNewTask = useCallback((sourceTaskId) => {
    if (isAnalyzing) {
      return;
    }
    if (taskManagement?.createNewTask) {
      taskManagement.createNewTask(sourceTaskId);
    } else {
      effectiveSetTasks((prev) => {
        const nextNumber = prev.length + 1;
        const sourcePrompt = sourceTaskId
          ? prev.find((task) => task.id === sourceTaskId)?.prompt
          : prev[prev.length - 1]?.prompt ?? "";
        const nextTask = createTask(nextNumber, sourcePrompt ?? "", sourceTaskId);

        const updated = prev.map((task) => ({
          ...task,
          isOpen: false,
        }));

        const newTasks = [...updated, nextTask];
        effectiveSetActiveTaskId(nextTask.id);
        return newTasks;
      });
    }
  }, [isAnalyzing, taskManagement, effectiveSetTasks, effectiveSetActiveTaskId]);

  const handleSelectTask = useCallback((taskId) => {
    if (taskManagement?.selectTask) {
      taskManagement.selectTask(taskId);
    } else {
      effectiveSetActiveTaskId(taskId);
      effectiveSetTasks((prev) =>
        prev.map((task) => ({
          ...task,
          isOpen: task.id === taskId,
        }))
      );
    }
  }, [taskManagement, effectiveSetActiveTaskId, effectiveSetTasks]);

  const handleDiscardTask = useCallback(
    (taskId) => {
      setTasks((prev) => {
        const remaining = prev.filter((task) => task.id !== taskId);
        const normalized = (remaining.length ? remaining : [createTask(1)]).map((task, index) => ({
          ...task,
          number: index + 1,
        }));
        return normalized;
      });
      setIsConfirmDiscardOpen(false);
      setConfirmDiscardTaskId(null);
      onShowToast?.({
        title: "Task deleted",
        message: "The task was deleted.",
        type: "info",
        autoClose: true,
      });
    },
    [onShowToast]
  );

  const handleRenameTask = useCallback((taskId) => {
    const task = effectiveTasks?.find((t) => t.id === taskId);
    if (task) {
      setRenameTaskId(taskId);
      setRenameTaskTitle(task.title || `Task #${task.number}`);
      setIsRenameModalOpen(true);
    }
  }, [effectiveTasks]);

  const handleConfirmRename = useCallback(() => {
    if (renameTaskId && renameTaskTitle.trim() && taskManagement?.renameTask) {
      taskManagement.renameTask(renameTaskId, renameTaskTitle.trim());
      setIsRenameModalOpen(false);
      setRenameTaskId(null);
      setRenameTaskTitle("");
      onShowToast?.({
        title: "Renamed",
        message: "The task name was updated.",
        type: "success",
        autoClose: true,
      });
    }
  }, [renameTaskId, renameTaskTitle, taskManagement, onShowToast]);

  const handleDeleteTask = useCallback((taskId) => {
    setDeleteTaskId(taskId);
    setIsDeleteModalOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(() => {
    if (deleteTaskId && taskManagement?.deleteTask) {
      taskManagement.deleteTask(deleteTaskId);
      setIsDeleteModalOpen(false);
      setDeleteTaskId(null);
      onShowToast?.({
        title: "Deleted",
        message: "The task was deleted.",
        type: "info",
        autoClose: true,
      });
    }
  }, [deleteTaskId, taskManagement, onShowToast]);

  // 监听来自 Sidebar 的 rename 和 delete 事件
  useEffect(() => {
    const handleRenameEvent = (event) => {
      handleRenameTask(event.detail.taskId);
    };
    const handleDeleteEvent = (event) => {
      handleDeleteTask(event.detail.taskId);
    };

    window.addEventListener('renameTask', handleRenameEvent);
    window.addEventListener('deleteTask', handleDeleteEvent);

    return () => {
      window.removeEventListener('renameTask', handleRenameEvent);
      window.removeEventListener('deleteTask', handleDeleteEvent);
    };
  }, [handleRenameTask, handleDeleteTask]);

  const isUploadReady = useMemo(
    () => Boolean(uploadedFile?.fileId && selectedSheets.length > 0),
    [uploadedFile?.fileId, selectedSheets.length]
  );

  return (
    <div className="w-full">
      <div className="flex items-center mb-3">
        <div className="w-7 h-7 rounded-full bg-blue-600 text-white text-sm font-semibold flex items-center justify-center mr-3">
          2
        </div>
        <h2 className="text-lg font-semibold text-slate-800">AI Data Analysis</h2>
      </div>

      {/* ⚠️ 添加任务切换动画：使用 key 触发重新渲染 */}
      <div
        key={activeTaskId || 'no-task'}
        className="animate-task-switch"
      >
        <TaskContentPanel
          task={activeTask}
          uploadedFile={selectedSheets.length > 0 ? uploadedFile : null}
          selectedSheets={selectedSheets}
          isUploadReady={isUploadReady}
          isAnalyzing={isAnalyzing}
          progressMsg={progressMsg}
          progressSteps={progressSteps}
          onTaskPromptChange={handleTaskPromptChange}
          onAnalyzeTask={handleAnalyzeTask}
          onTaskPageSizeChange={handleTaskPageSizeChange}
          onTaskPageChange={handleTaskPageChange}
          onStepPage={handleStepPage}
          onExportPreview={handleExportPreview}
          onTaskChartTypeChange={handleTaskVisualizationModeChange}
          onTaskChartPanelToggle={(taskId, view) => {
            effectiveUpdateTask(taskId, () => ({ isChartDataView: view === "data" }));
          }}
          onTaskChartDataPageSizeChange={handleTaskChartDataPageSizeChange}
          onTaskChartDataPageChange={handleTaskChartDataPageChange}
          onExportChartImage={handleExportChartImage}
          onExportChartData={handleExportChartData}
          exportMenuKey={exportMenuKey}
          onToggleExportMenu={toggleExportMenuForTask}
          chartContainerRefs={chartContainerRefs}
          onUpdateTask={effectiveUpdateTask}
        />
      </div>

      {isConfirmDiscardOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="sm-dialog w-full max-w-sm space-y-4 p-6" role="alertdialog" aria-modal="true" aria-labelledby="discard-task-title">
            <div>
              <h4 id="discard-task-title" className="text-base font-semibold text-slate-900">Discard task?</h4>
              <p className="mt-1 text-sm text-slate-600">
                This will remove the selected task and its results. You can't undo this action.
                {confirmDiscardTaskId && (
                  <>
                    {" "}
                    (Includes Task #
                    {tasks.find((task) => task.id === confirmDiscardTaskId)?.number ?? ""}
                    )
                  </>
                )}
              </p>
            </div>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setIsConfirmDiscardOpen(false);
                  setConfirmDiscardTaskId(null);
                }}
                className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-md hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
                <button
                  type="button"
                onClick={() => handleDiscardTask(confirmDiscardTaskId)}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-md transition-colors"
              >
                Discard Task
                </button>
            </div>
          </div>
        </div>
      )}

      {isRenameModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="sm-dialog w-full max-w-sm space-y-4 p-6" role="dialog" aria-modal="true" aria-labelledby="rename-task-title">
            <div>
              <h4 id="rename-task-title" className="text-base font-semibold text-slate-900">Rename Task</h4>
              <p className="mt-1 text-sm text-slate-600">Enter a new name for this task.</p>
            </div>
            <div>
              <input
                type="text"
                value={renameTaskTitle}
                onChange={(e) => setRenameTaskTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    handleConfirmRename();
                  } else if (e.key === "Escape") {
                    setIsRenameModalOpen(false);
                    setRenameTaskTitle("");
                  }
                }}
                className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Task name"
                autoFocus
              />
              </div>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setIsRenameModalOpen(false);
                  setRenameTaskTitle("");
                  setRenameTaskId(null);
                }}
                className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-md hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmRename}
                disabled={!renameTaskTitle.trim()}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed rounded-md transition-colors"
              >
                Rename
              </button>
            </div>
          </div>
        </div>
      )}

      {isDeleteModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="sm-dialog w-full max-w-sm space-y-4 p-6" role="alertdialog" aria-modal="true" aria-labelledby="delete-task-title">
            <div>
              <h4 id="delete-task-title" className="text-base font-semibold text-slate-900">Delete Task</h4>
              <p className="mt-1 text-sm text-slate-600">
                Are you sure you want to delete this task? This action cannot be undone.
                {deleteTaskId && (
                  <>
                    {" "}
                    (Task #
                    {effectiveTasks.find((task) => task.id === deleteTaskId)?.number ?? ""}
                    )
                  </>
                )}
              </p>
            </div>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setIsDeleteModalOpen(false);
                  setDeleteTaskId(null);
                }}
                className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-md hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDelete}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-md transition-colors"
              >
                Delete
              </button>
                </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default AIDataAnalysis;

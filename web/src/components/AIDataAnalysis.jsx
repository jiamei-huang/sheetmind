import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import TaskContentPanel from "./AIDataAnalysis/TaskContentPanel";
import { analyzeStream, toAnalysisErrorMessage } from "../api/analysis";
import { createTask as createTaskApi } from "../api/tasks";
import { toAnalysisViewModel } from "../api/resultBlocks";
import { isValidBackendProjectId } from "../utils/validation";
import { createTask, PAGE_SIZE_OPTIONS } from "../constants/task";
import { CHART_TYPES, CHART_TYPE_LABELS } from "../constants/chart";
import { classifyPrompt } from "../utils/prompt";

const maxChars = 500;

function AIDataAnalysis({
  uploadedFile,
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
  const [exportMenuTaskId, setExportMenuTaskId] = useState(null);
  const chartContainerRefs = useRef({});
  const [isRenameModalOpen, setIsRenameModalOpen] = useState(false);
  const [renameTaskId, setRenameTaskId] = useState(null);
  const [renameTaskTitle, setRenameTaskTitle] = useState("");
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [deleteTaskId, setDeleteTaskId] = useState(null);

  useEffect(() => {
    if (!exportMenuTaskId) {
      return undefined;
    }

    const handleClickOutside = () => {
      setExportMenuTaskId(null);
    };

    document.addEventListener("click", handleClickOutside);
    return () => {
      document.removeEventListener("click", handleClickOutside);
    };
  }, [exportMenuTaskId]);

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

  // updateTask 现在从 taskManagement 获取或使用本地版本

  const handleTaskPromptChange = useCallback((taskId, value) => {
    effectiveSetTasks((prev) =>
      prev.map((task) =>
        task.id === taskId
          ? {
              ...task,
              prompt: value.slice(0, maxChars),
              classification: task.status === "completed" ? task.classification : null,
            }
          : task
      )
    );
  }, [effectiveSetTasks]);

  const handleSuggestionSelect = useCallback(
    (taskId, suggestion) => {
      const ready = Boolean(uploadedFile?.fileId && selectedSheets.length > 0);
      if (!ready || isAnalyzing) {
        return;
      }
      effectiveSetTasks((prev) =>
        prev.map((task) => (task.id === taskId ? { ...task, prompt: suggestion } : task))
      );
    },
    [isAnalyzing, selectedSheets, uploadedFile?.fileId, effectiveSetTasks]
  );

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


  const handleExportPreview = useCallback((taskId) => {
    const targetTask = effectiveTasks.find((task) => task.id === taskId);
    const lastResult = targetTask?.results && targetTask.results.length > 0
      ? targetTask.results[targetTask.results.length - 1]
      : null;
    if (!lastResult) {
      return;
    }
    onShowToast?.({
      title: "导出中",
      message: `正在导出数据预览（${lastResult.fileName || "analysis"}）`,
      type: "info",
      autoClose: true,
    });
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
    async (taskId, resultIndex) => {
      const targetTask = effectiveTasks.find((task) => task.id === taskId);
      const results = targetTask?.results || [];
      const idx = resultIndex ?? results.length - 1;
      const result = results[idx] ?? null;
      if (!result?.chartData) {
        onShowToast?.({ title: "导出失败", message: "无图表数据可导出", type: "error" });
        return;
      }
      const chartData = result.chartDatas?.[targetTask.activeChartIndex ?? 0] ?? result.chartData;

      try {
        onShowToast?.({ title: "导出中", message: "正在生成图表图片...", type: "info", autoClose: true });

        const container = chartContainerRefs.current[`${taskId}-${idx}`];
        if (!container) {
          onShowToast?.({ title: "导出失败", message: "无法定位图表区域，请稍后重试", type: "error" });
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
        onShowToast?.({ title: "导出成功", message: "图表图片已下载", type: "success", autoClose: true });
      } catch (err) {
        console.error("[Export Image]", err);
        onShowToast?.({ title: "导出失败", message: err?.message || "生成图片时出错", type: "error" });
      }
    },
    [onShowToast, effectiveTasks]
  );

  const handleExportChartData = useCallback(
    (taskId, resultIndex) => {
      const targetTask = effectiveTasks.find((task) => task.id === taskId);
      const results = targetTask?.results || [];
      const idx = resultIndex ?? results.length - 1;
      const result = results[idx] ?? null;
      if (!result?.chartData?.labels?.length || !result.chartData.series?.length) {
        onShowToast?.({ title: "导出失败", message: "无图表数据可导出", type: "error" });
        return;
      }
      const chartData = result.chartDatas?.[targetTask.activeChartIndex ?? 0] ?? result.chartData;
      const { labels, series } = chartData;

      try {
        const headers = ["Label", ...series.map((s) => s.name)];
        const rows = labels.map((label, i) => {
          const values = series.map((s) => {
            const v = s.values?.[i];
            if (v === null || v === undefined) return "";
            return typeof v === "number" ? String(v) : String(v).replace(/"/g, '""');
          });
          const safeLabel = String(label ?? "").replace(/"/g, '""');
          return [safeLabel, ...values];
        });
        const csvContent = [headers.join(","), ...rows.map((r) => r.map((c) => `"${c}"`).join(","))].join("\n");
        const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `chart-data-${taskId}-${idx}.csv`;
        link.click();
        URL.revokeObjectURL(url);
        onShowToast?.({ title: "导出成功", message: "图表数据已下载为 CSV", type: "success", autoClose: true });
      } catch (err) {
        console.error("[Export Data]", err);
        onShowToast?.({ title: "导出失败", message: err?.message || "导出数据时出错", type: "error" });
      }
    },
    [onShowToast, effectiveTasks]
  );

  const toggleExportMenuForTask = useCallback((taskId) => {
    setExportMenuTaskId((prev) => (prev === taskId ? null : taskId));
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
        message: "未找到任务，请先创建任务",
        title: "任务错误",
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
        title: "缺少必要信息",
        message: "请先上传Excel文件并选择至少一个Sheet",
        type: "info",
        autoClose: true,
      });
      return;
    }

    const trimmedPrompt = task.prompt.trim();
    if (!trimmedPrompt) {
      // 轻量提示：使用Toast（自动关闭）
      onShowToast?.({
        message: "请输入要处理或分析的内容描述",
        type: "info",
        autoClose: true,
      });
      return;
    }

    const classification = classifyPrompt(trimmedPrompt);

    effectiveSetTasks((prev) =>
      prev.map((item) =>
        item.id === taskId
          ? {
              ...item,
              prompt: trimmedPrompt,
              status: "running",
              classification: classification.label,
              mode: classification.mode,
              analysisError: null,
            }
          : item
      )
    );
    setIsAnalyzing(true);
    setProgressMsg("正在准备分析...");
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
            message: "项目ID缺失，无法创建任务。请确保已上传Excel文件。",
            title: "任务错误",
          });
          return;
        }


        if (!isValidBackendProjectId(currentProjectId)) {
          console.error("[AIDataAnalysis] ProjectId is not backend UUID:", currentProjectId);
          console.error("[AIDataAnalysis] uploadedFile:", uploadedFile);
          console.error("[AIDataAnalysis] activeProjectId from props:", activeProjectId);
          onShowErrorModal?.({
            message: `当前项目不是后端项目（项目ID: ${currentProjectId}），无法创建任务。请先上传Excel文件创建项目。如果已上传，请刷新页面。`,
            title: "任务错误",
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
            message: `创建任务失败：${createError?.response?.data?.detail || createError?.message || "未知错误"}`,
            title: "任务创建失败",
          });
          return;
        }
      }

      if (!actualTaskId) {
        // 重要错误：使用弹窗（手动关闭）
        onShowErrorModal?.({
          message: "任务ID缺失，请先创建任务",
          title: "任务错误",
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
              status: "draft", // 保持draft状态以便继续对话
              results: [...(item.results || []), newResult],
              prompt: "", // 清空prompt以便继续输入
              analysisError: null,
              isOpen: true,
              pageSize: item.pageSize ?? PAGE_SIZE_OPTIONS[0],
              currentPage: 1,
              selectedChartType: defaultChartType,
              isChartDataView: false,
            };
          }
          return item;
        });
      });

      // 轻量提示：使用Toast（自动关闭）
      onShowToast?.({
        title: "分析完成",
        message: "预览已就绪，可以查看结果",
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
                status: "draft",
                analysisError: message,
              }
            : item
        )
      );

      // 重要错误：使用弹窗（手动关闭）
      onShowErrorModal?.({
        message,
        title: "分析未完成",
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
        title: "任务已删除",
        message: "任务已成功删除",
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
        title: "重命名成功",
        message: "任务名称已更新",
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
        title: "删除成功",
        message: "任务已成功删除",
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
    <div className="w-full mt-8">
      <div className="flex items-center mb-4">
        <div className="w-7 h-7 rounded-full bg-blue-600 text-white text-sm font-semibold flex items-center justify-center mr-3">
          2
        </div>
        <h2 className="text-lg font-semibold text-gray-800">AI Data Analysis</h2>
      </div>

      {/* ⚠️ 添加任务切换动画：使用 key 触发重新渲染 */}
      <div
        key={activeTaskId || 'no-task'}
        className="bg-white rounded-xl shadow-sm animate-task-switch"
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
          onSuggestionSelect={handleSuggestionSelect}
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
          exportMenuTaskId={exportMenuTaskId}
          onToggleExportMenu={toggleExportMenuForTask}
          chartContainerRefs={chartContainerRefs}
          onUpdateTask={effectiveUpdateTask}
        />
      </div>

      {isConfirmDiscardOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6 space-y-4">
            <div>
              <h4 className="text-base font-semibold text-gray-900">Discard task?</h4>
              <p className="mt-1 text-sm text-gray-600">
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
                className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-md hover:bg-gray-100 transition-colors"
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
          <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6 space-y-4">
            <div>
              <h4 className="text-base font-semibold text-gray-900">Rename Task</h4>
              <p className="mt-1 text-sm text-gray-600">Enter a new name for this task.</p>
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
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
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
                className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-md hover:bg-gray-100 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmRename}
                disabled={!renameTaskTitle.trim()}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed rounded-md transition-colors"
              >
                Rename
              </button>
            </div>
          </div>
        </div>
      )}

      {isDeleteModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6 space-y-4">
            <div>
              <h4 className="text-base font-semibold text-gray-900">Delete Task</h4>
              <p className="mt-1 text-sm text-gray-600">
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
                className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-md hover:bg-gray-100 transition-colors"
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

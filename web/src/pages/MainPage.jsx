import React, { useCallback, useMemo, useRef, useState } from "react";
import Header from "../components/Header";
import FileUploader from "../components/UploadSection/FileUploader";
import AIDataAnalysis from "../components/AIDataAnalysis";
import Toast from "../components/Toast";
import ErrorModal from "../components/ErrorModal";
import Sidebar from "../components/Sidebar";
import { useTaskManagement } from "../hooks/useTaskManagement";
import { useProjectManagement } from "../hooks/useProjectManagement";
import { useFileManagement } from "../hooks/useFileManagement";

/**
 * 主应用页面：Excel 上传 + AI 数据分析
 */
export default function MainPage() {
  const [toast, setToast] = useState(null);
  const [errorModal, setErrorModal] = useState(null);
  const [isNavigationOpen, setIsNavigationOpen] = useState(false);
  const suppressTaskReloadRef = useRef(false);

  const projectManagement = useProjectManagement();
  const taskManagement = useTaskManagement(
    projectManagement.activeProjectId,
    projectManagement.isReady,
    suppressTaskReloadRef
  );
  const fileManagement = useFileManagement(projectManagement.activeProjectId);

  const {
    filesByProjectRef,
    uploadedFiles,
    handleFilesChange,
    forceUpdate,
    setPrevProjectId,
  } = fileManagement;

  const uploadedFilesCount = uploadedFiles.length;
  const selectedSheetsTotal = useMemo(
    () =>
      uploadedFiles.reduce(
        (total, file) => total + (file.selectedSheets?.length ?? 0),
        0
      ),
    [uploadedFiles]
  );

  const analysisTargetFile = useMemo(() => {
    if (!uploadedFilesCount) return null;
    return uploadedFiles.find((file) => file.selectedSheets?.length) ?? uploadedFiles[0];
  }, [uploadedFiles, uploadedFilesCount]);

  const showToast = useCallback(({ message, title, type = "info", autoClose = true }) => {
    if (type === "error" && !autoClose) {
      setErrorModal({ id: Date.now(), message, title });
      return;
    }
    setToast({ id: Date.now(), message, title, type, autoClose });
  }, []);

  const showErrorModal = useCallback(({ message, title = "Error" }) => {
    setErrorModal({ id: Date.now(), message, title });
  }, []);

  const handleDismissToast = useCallback(() => setToast(null), []);
  const handleCloseErrorModal = useCallback(() => setErrorModal(null), []);

  const handleProjectCreated = useCallback(
    async (projectId, projectName) => {
      if (!projectId || !projectManagement.setActiveProjectId) return;

      const currentActiveProjectId = projectManagement.activeProjectId;
      const tempId = currentActiveProjectId;
      const realId = projectId;
      const isFrontendTemp =
        tempId?.startsWith("project-") &&
        realId &&
        realId !== tempId &&
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(tempId);

      if (isFrontendTemp) {
        const tempFiles = filesByProjectRef.current[tempId] || [];
        const realFiles = filesByProjectRef.current[realId] || [];
        const mergeById = (a, b) => {
          const m = new Map();
          (a || []).forEach((x) => m.set(x.id, x));
          (b || []).forEach((x) => m.set(x.id, x));
          return Array.from(m.values());
        };
        const mergedFiles = mergeById(realFiles, tempFiles);
        filesByProjectRef.current[realId] = mergedFiles;
        delete filesByProjectRef.current[tempId];
        localStorage.setItem(`excel_${realId}`, JSON.stringify(mergedFiles));
        localStorage.removeItem(`excel_${tempId}`);

        if (taskManagement.migrateTempToReal) {
          await taskManagement.migrateTempToReal(tempId, realId);
        }
        forceUpdate();
      }

      if (projectManagement.setProjects) {
        projectManagement.setProjects((prev) => {
          const existsById = prev.some((p) => p.id === projectId);
          if (existsById) return prev;

          const isFrontendTemporaryProject =
            currentActiveProjectId?.startsWith("project-") &&
            !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
              currentActiveProjectId
            );

          if (projectName === null) {
            if (isFrontendTemporaryProject) {
              const currentProject = prev.find((p) => p.id === currentActiveProjectId);
              if (currentProject) {
                return prev.map((p) =>
                  p.id === currentActiveProjectId ? { ...p, id: projectId } : p
                );
              }
            }
            return prev;
          }

          if (isFrontendTemporaryProject) {
            const currentProject = prev.find((p) => p.id === currentActiveProjectId);
            if (currentProject) {
              return prev.map((p) =>
                p.id === currentActiveProjectId
                  ? { id: projectId, name: projectName, createdAt: Date.now() }
                  : p
              );
            }
          }

          const baseProjectName = (projectName || "").replace(/\s*\(\d+\)$/, "");
          const sameNameProject = prev.find((p) => {
            const pBaseName = (p.name || "").replace(/\s*\(\d+\)$/, "");
            return (
              pBaseName === baseProjectName &&
              p.id.startsWith("project-") &&
              !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(p.id)
            );
          });
          if (sameNameProject) {
            return prev.map((p) =>
              p.id === sameNameProject.id
                ? { id: projectId, name: projectName, createdAt: Date.now() }
                : p
            );
          }

          return [
            ...prev,
            {
              id: projectId,
              name: projectName || `Project_${new Date().toISOString().split("T")[0]}`,
              createdAt: Date.now(),
            },
          ];
        });
      }

      projectManagement.setActiveProjectId(projectId);
      setPrevProjectId(projectId);
    },
    [
      projectManagement,
      taskManagement,
      filesByProjectRef,
      forceUpdate,
      setPrevProjectId,
    ]
  );

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <Header onOpenNavigation={() => setIsNavigationOpen(true)} />

      <div className="flex min-h-0 flex-1 pt-[60px]">
        <Sidebar
          projects={projectManagement.projects}
          activeProjectId={projectManagement.activeProjectId}
          onSelectProject={projectManagement.selectProject}
          onCreateProject={projectManagement.createNewProject}
          onRenameProject={projectManagement.renameProject}
          onDeleteProject={projectManagement.deleteProject}
          tasks={taskManagement.tasks}
          activeTaskId={taskManagement.activeTaskId}
          onSelectTask={taskManagement.selectTask}
          onCreateTask={() => taskManagement.createNewTask(taskManagement.activeTaskId)}
          onRenameTask={(taskId) => {
            window.dispatchEvent(new CustomEvent("renameTask", { detail: { taskId } }));
          }}
          onDeleteTask={(taskId) => {
            window.dispatchEvent(new CustomEvent("deleteTask", { detail: { taskId } }));
          }}
          isOpen={isNavigationOpen}
          onClose={() => setIsNavigationOpen(false)}
        />

        <main
          key={projectManagement.activeProjectId || "no-project"}
          className="min-w-0 flex-1 overflow-y-auto leading-normal animate-project-switch lg:ml-72"
        >
          <Toast
            key={toast?.id}
            message={toast?.message}
            title={toast?.title}
            type={toast?.type}
            onDismiss={handleDismissToast}
            autoClose={toast?.autoClose !== false}
          />
          <ErrorModal
            key={errorModal?.id}
            message={errorModal?.message}
            title={errorModal?.title}
            onClose={handleCloseErrorModal}
          />

          <section className="mx-auto w-full max-w-7xl px-4 pb-40 pt-5 sm:px-6 sm:pt-6 lg:px-8">
            <div className="mb-6">
              <h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">
                Ask questions. Find insights in your Excel data.
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
                Upload one or more Excel workbooks to compare data, calculate metrics, and uncover patterns with AI.
              </p>
            </div>

            <div className="mb-5">
              <FileUploader
                uploadedFiles={uploadedFiles}
                onFilesChange={handleFilesChange}
                onShowToast={showToast}
                onShowErrorModal={showErrorModal}
                activeProjectId={projectManagement.activeProjectId}
                projectManagement={projectManagement}
                suppressTaskReloadRef={suppressTaskReloadRef}
                onProjectCreated={handleProjectCreated}
                isProjectReady={projectManagement.isReady}
              />
            </div>

            <AIDataAnalysis
              uploadedFile={analysisTargetFile}
              uploadedFiles={uploadedFiles}
              activeProjectId={projectManagement.activeProjectId}
              uploadedFilesCount={uploadedFilesCount}
              selectedSheetsTotal={selectedSheetsTotal}
              hasUploadedFiles={uploadedFilesCount > 0}
              onShowToast={showToast}
              onShowErrorModal={showErrorModal}
              taskManagement={taskManagement}
            />
          </section>
        </main>
      </div>
    </div>
  );
}

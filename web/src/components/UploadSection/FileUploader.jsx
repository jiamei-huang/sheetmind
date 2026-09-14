import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Upload, FileSpreadsheet, Plus, ChevronDown, ChevronUp } from "lucide-react";
import FileCard from "./FileCard";
import { deleteExcelFile, uploadExcelFile } from "../../api/files";
import { isValidBackendProjectId } from "../../utils/validation";
import {
  oversizedUploadMessage,
  oversizedUploadNames,
  unsupportedUploadMessage,
  unsupportedUploadNames,
} from "../../utils/fileValidation";

const MAX_FILES = 5;

/**
 * 格式化文件大小
 * @param {number} bytes - 文件大小（字节）
 * @returns {string} 格式化后的文件大小（如 "1.5 MB"）
 */
const formatFileSize = (bytes) => {
  if (!bytes || bytes === 0) return "0 B";

  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));

  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
};

export default function FileUploader({
  onFilesChange,
  onShowToast,
  onShowErrorModal,
  activeProjectId,
  onProjectCreated, // (projectId, projectName?) => void
  uploadedFiles: externalUploadedFiles, // 从父组件接收文件列表
  projectManagement, // 项目管理对象，用于获取项目名称
  suppressTaskReloadRef, // 禁止上传 Excel 时触发任务加载
  isProjectReady = true,
}) {
  const fileInputRef = useRef(null);
  const [internalUploadedFiles, setInternalUploadedFiles] = useState([]);
  const uploadedFiles = externalUploadedFiles !== undefined ? externalUploadedFiles : internalUploadedFiles;
  const uploadedFilesRef = useRef(uploadedFiles);
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const selectedSheetCount = uploadedFiles.reduce(
    (total, file) => total + (file.selectedSheets?.length ?? 0),
    0
  );
  const [isDataManagerOpen, setIsDataManagerOpen] = useState(true);

  useEffect(() => {
    uploadedFilesRef.current = uploadedFiles;
  }, [uploadedFiles]);

  const hasReachedLimit = uploadedFiles.length >= MAX_FILES;

  const rejectUnsupportedFiles = useCallback((files) => {
    const unsupported = unsupportedUploadNames(files);
    if (!unsupported.length) return false;
    onShowErrorModal?.({
      title: "Unsupported file type",
      message: unsupportedUploadMessage(unsupported),
    });
    return true;
  }, [onShowErrorModal]);

  const rejectOversizedFiles = useCallback((files) => {
    const oversized = oversizedUploadNames(files);
    if (!oversized.length) return false;
    onShowErrorModal?.({
      title: "File too large",
      message: oversizedUploadMessage(oversized),
    });
    return true;
  }, [onShowErrorModal]);

  const handleBrowseClick = () => {
    if (!isProjectReady || isUploading) return;
    if (hasReachedLimit) {
      onShowToast?.({
        title: "File limit reached",
        message: `You can upload up to ${MAX_FILES} files.`,
        type: "info",
        autoClose: true,
      });
      return;
    }
    fileInputRef.current?.click();
  };

  // ⚠️ 原子化操作：只操作当前项目的文件，禁止跨项目合并
  // ⚠️ 使用 useRef 存储当前有效的项目ID（可能是前端临时ID或后端UUID）
  const effectiveProjectIdRef = useRef(activeProjectId);

  // 当 activeProjectId 变化时，更新 effectiveProjectIdRef
  useEffect(() => {
    effectiveProjectIdRef.current = activeProjectId;
  }, [activeProjectId]);

  const handleFilesUpdated = useCallback(
    (updater, targetProjectId = null) => {
      const updateFiles = (prev) => {
        // 优先使用传入的 targetProjectId（通常是后端UUID），否则使用 effectiveProjectIdRef.current
        // 这样可以处理项目ID从前端临时ID切换到后端UUID的情况
        const currentProjectId = targetProjectId || effectiveProjectIdRef.current || activeProjectId;
        if (!currentProjectId) {
          return prev;
        }


        const belongsToCurrentProject = (file) =>
          (file.projectId || currentProjectId) === currentProjectId;
        const filteredPrev = Array.isArray(prev)
          ? prev.filter(belongsToCurrentProject)
          : prev;

        const nextFiles = typeof updater === "function" ? updater(filteredPrev) : updater;


        const filteredNextFiles = Array.isArray(nextFiles)
          ? nextFiles.filter(belongsToCurrentProject)
          : nextFiles;

        onFilesChange?.(filteredNextFiles, currentProjectId);
        return filteredNextFiles;
      };

      // 如果使用外部文件列表，只通知父组件；否则更新内部状态
      if (externalUploadedFiles !== undefined) {
        const nextFiles = updateFiles(uploadedFilesRef.current);
        uploadedFilesRef.current = nextFiles;
      } else {
        setInternalUploadedFiles(updateFiles);
      }
    },
    [onFilesChange, externalUploadedFiles, activeProjectId]
  );

  const handleFileUpload = useCallback(
    async (file, targetProjectIdOverride = null) => {
      if (!file) {
        onShowErrorModal?.({
          message: "Choose an Excel file to upload.",
          title: "Upload error",
        });
        return;
      }
      if (rejectUnsupportedFiles([file])) return;
      if (rejectOversizedFiles([file])) return;

      const name = file.name;
      if (!isProjectReady) return;
      setIsUploading(true);

      try {

        const uploadProjectId = targetProjectIdOverride || activeProjectId;
        const isExistingProject = uploadProjectId && isValidBackendProjectId(uploadProjectId);

        // 如果 activeProjectId 不是有效的UUID（比如是前端临时ID），需要先获取项目名称
        // 然后创建后端项目，再上传文件
        let targetProjectId = uploadProjectId;
        let targetProjectName = undefined;

        if (!isExistingProject && uploadProjectId) {
          // 前端临时项目，需要先获取项目名称，然后创建后端项目
          const frontendProject = projectManagement?.projects?.find(p => p.id === uploadProjectId);
          if (frontendProject) {
            targetProjectName = frontendProject.name;
          } else {
            // 如果找不到项目，使用默认名称
            targetProjectName = `Project_${new Date().toISOString().split("T")[0]}`;
          }
        } else {
        }


        // 在 FileUploader 调用前设置
        if (suppressTaskReloadRef) {
          suppressTaskReloadRef.current = true;
        }

        // 调用真实后端API上传文件
        const response = await uploadExcelFile({
          file: file,
          projectId: targetProjectId, // 如果已有后端项目，使用现有项目ID；否则为undefined，会创建新项目
          projectName: targetProjectName, // 如果是前端临时项目，使用项目名称创建后端项目
        });


        // 处理响应：response.files 是数组，每个元素包含 fileName 和 sheets
        const fileInfo = response.files?.[0] || response.files?.[response.files.length - 1];
        // 从后端响应中获取sheets，如果没有则使用空数组
        const availableSheets = fileInfo?.sheets || response.sheets || [];

        // 获取真实的项目ID（优先使用响应中的，否则使用当前的 activeProjectId）
        const realProjectId = response.projectId || activeProjectId;

        // 无论是否创建新项目，只要当前是前端临时项目，都需要更新项目ID
        // 如果创建了新项目，使用新的项目ID和名称
        // 如果上传到已有项目（包括同名项目），使用已有项目的ID（不更新名称）

        // 检查当前项目ID是否是后端UUID（复用上面的函数）
        const isCurrentProjectIdBackend = isValidBackendProjectId(uploadProjectId);
        const isResponseProjectIdBackend = isValidBackendProjectId(response.projectId);

        // 确保使用正确的项目ID（优先使用响应中的后端UUID，否则使用当前activeProjectId）
        const finalProjectId = response.projectId || uploadProjectId;

        // 如果当前是前端临时项目，且响应中有后端UUID，则更新项目ID
        if (!isCurrentProjectIdBackend && isResponseProjectIdBackend && response.projectId) {
          if (!response.isExistingProject) {
            // 创建了新项目，使用新的项目名称
            const finalProjectName = response.projectName || targetProjectName || `Project_${new Date().toISOString().split("T")[0]}`;
            await onProjectCreated?.(response.projectId, finalProjectName);
          } else {
            // 上传到已有项目（包括同名项目），不更新项目名称
            await onProjectCreated?.(response.projectId, null);
          }
        } else if (isCurrentProjectIdBackend && isResponseProjectIdBackend && uploadProjectId !== response.projectId) {
          // 如果当前项目ID是后端UUID，但与响应中的项目ID不同，也更新
          await onProjectCreated?.(response.projectId, null);
        } else {
        }

        // 获取实际文件大小
        const actualFileSize = file.size ? formatFileSize(file.size) : "Unknown";

        const newFile = {
          id: fileInfo?.fileId || fileInfo?.fileName || name,
          fileId: fileInfo?.fileId || fileInfo?.fileName || name,
          name: fileInfo?.fileName || response.fileName || name,
          size: actualFileSize, // 使用实际文件大小
          uploadedAt: new Date().toLocaleString(),
          sheets: availableSheets,
          selectedSheets: [],
          isExpanded: true,
          projectId: finalProjectId, // 使用最终确定的项目ID
        };

        // 无论是否创建新项目，都立即添加文件
        // 如果创建了新项目，使用新的项目ID（finalProjectId）添加文件
        // 如果上传到已有项目，也使用 finalProjectId 添加文件
        let inserted = false;
        const uploadStatus = fileInfo?.uploadStatus || "created";

        // 传递 finalProjectId（后端UUID）给 handleFilesUpdated
        // 因为此时 activeProjectId 可能还是前端临时ID，但新文件的 projectId 已经是后端UUID
        handleFilesUpdated((prev) => {
          // prev 已经在 handleFilesUpdated 内部过滤过了，但为了双重保险，再次确认
          // 实际上，由于 handleFilesUpdated 已经过滤，这里的 prev 应该只包含当前项目的文件

          const existingIndex = prev.findIndex(
            (item) => item.fileId === newFile.fileId
          );
          if (existingIndex >= 0) {
            inserted = true;
            if (uploadStatus === "reused") return prev;
            return prev.map((item, index) =>
              index === existingIndex
                ? { ...newFile, selectedSheets: item.selectedSheets || [] }
                : item
            );
          }
          if (prev.length >= MAX_FILES) {
            return prev;
          }
          inserted = true;
          // 只添加新文件到当前项目的文件列表
          // newFile 的 projectId 已经设置为 finalProjectId，所以是安全的
          return [newFile, ...prev];
        }, finalProjectId); // ⚠️ 传递 finalProjectId（后端UUID）作为 targetProjectId

        if (!inserted) {
          // 如果因为文件数量限制没有插入，显示提示
          onShowToast?.({
            title: "File limit reached",
            message: `You can upload up to ${MAX_FILES} files. Delete one before uploading another.`,
            type: "info",
            autoClose: true,
          });
          return; // 提前返回，不显示成功提示
        }

        // 显示成功提示（如果文件已插入或创建了新项目）
        if (inserted) {
          const message = `${newFile.name} uploaded successfully.`;
          // 轻量提示：使用Toast（自动关闭）
          onShowToast?.({
            title: "Upload complete",
            message,
            type: "success",
            autoClose: true,
          });
        }

        if (suppressTaskReloadRef) {
          setTimeout(() => {
            suppressTaskReloadRef.current = false;
          }, 200);
        }
        return finalProjectId;
      } catch (error) {
        const message =
          error?.response?.data?.detail ||
          error?.response?.data?.message ||
          error?.message ||
          "File upload failed. Please try again.";

        // 重要错误：使用弹窗（手动关闭）
        onShowErrorModal?.({
          message: `File upload failed: ${message}`,
          title: "Upload error",
        });

        if (suppressTaskReloadRef) {
          setTimeout(() => {
            suppressTaskReloadRef.current = false;
          }, 200);
        }
        return null;
      } finally {
        setIsUploading(false);
      }
    },
    [handleFilesUpdated, onShowToast, onShowErrorModal, activeProjectId, onProjectCreated, suppressTaskReloadRef, projectManagement, isProjectReady, rejectUnsupportedFiles, rejectOversizedFiles]
  );

  const handleFileInputChange = async (event) => {
    const files = Array.from(event.target.files ?? []);
    if (!files.length) {
      // 如果没有选择文件，不显示错误（用户可能只是取消了选择）
      return;
    }
    if (rejectUnsupportedFiles(files)) {
      event.target.value = "";
      return;
    }
    if (rejectOversizedFiles(files)) {
      event.target.value = "";
      return;
    }

    let uploadProjectId = isValidBackendProjectId(activeProjectId)
      ? activeProjectId
      : null;
    for (const file of files) {
      // eslint-disable-next-line no-await-in-loop
      uploadProjectId = await handleFileUpload(file, uploadProjectId) || uploadProjectId;
    }

    event.target.value = "";
  };

  const handleDrop = async (event) => {
    event.preventDefault();
    setIsDragging(false);
    if (!isProjectReady || isUploading) return;

    const droppedFiles = Array.from(event.dataTransfer?.files ?? []);
    if (!droppedFiles.length) {
      onShowErrorModal?.({
        message: "Choose an Excel file to upload.",
        title: "Upload error",
      });
      return;
    }
    if (rejectUnsupportedFiles(droppedFiles)) return;
    if (rejectOversizedFiles(droppedFiles)) return;

    let uploadProjectId = isValidBackendProjectId(activeProjectId)
      ? activeProjectId
      : null;
    for (const file of droppedFiles) {
      // eslint-disable-next-line no-await-in-loop
      uploadProjectId = await handleFileUpload(file, uploadProjectId) || uploadProjectId;
    }
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    if (!isDragging) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setIsDragging(false);
    }
  };

  const handleDeleteFile = async (fileId) => {
    const target = uploadedFilesRef.current.find((item) => item.id === fileId);
    if (!target) return;
    try {
      await deleteExcelFile({
        projectId: target.projectId || activeProjectId,
        fileId: target.fileId || target.id,
      });
      handleFilesUpdated((prev) => prev.filter((item) => item.id !== fileId));
      onShowToast?.({
        title: "File deleted",
        message: "The file was removed from the list.",
        type: "info",
        autoClose: true,
      });
    } catch (error) {
      onShowErrorModal?.({
        title: "Delete failed",
        message: error?.message || "Could not delete this file. Please try again.",
      });
    }
  };

  const handleToggleSheetPanel = (fileId, isExpanded) => {
    handleFilesUpdated((prev) =>
      prev.map((item) => (item.id === fileId ? { ...item, isExpanded } : item))
    );
  };

  const handleSelectedSheetsUpdate = (fileId, selectedSheets) => {
    handleFilesUpdated((prev) =>
      prev.map((item) =>
        item.id === fileId ? { ...item, selectedSheets, isExpanded: false } : item
      )
    );
    onShowToast?.({
      title: "Sheet selection saved",
      message: `${selectedSheets.length} ${selectedSheets.length === 1 ? "sheet" : "sheets"} selected.`,
      type: "success",
      autoClose: true,
    });
  };

  const helperText = useMemo(() => {
    if (hasReachedLimit) {
      return `Maximum of ${MAX_FILES} files reached. Delete a file to add more.`;
    }
    if (isUploading) {
      return "Processing upload...";
    }
    // 移除初始提示文字
    return "";
  }, [hasReachedLimit, isUploading]);

  const selectedSheetNames = useMemo(
    () => uploadedFiles.flatMap((file) => file.selectedSheets ?? []),
    [uploadedFiles]
  );

  const uploadedFileNames = useMemo(
    () => uploadedFiles.map((file) => file.name).join(", "),
    [uploadedFiles]
  );

  return (
    <div className="w-full">
      <div className="flex items-center mb-3">
        <div className="w-7 h-7 rounded-full bg-blue-600 text-white text-sm font-semibold flex items-center justify-center mr-3">
          1
        </div>
        <h2 className="text-base sm:text-lg font-semibold text-slate-800">Import Your Data</h2>
      </div>

      {uploadedFiles.length > 0 && !isDataManagerOpen ? (
        <div className="sm-panel flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 items-start gap-3">
            <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-green-50 text-green-600">
              <FileSpreadsheet className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900">
                {uploadedFiles.length} {uploadedFiles.length === 1 ? "file" : "files"} · {selectedSheetCount} {selectedSheetCount === 1 ? "sheet" : "sheets"} selected
              </p>
              <p className="truncate text-xs leading-5 text-slate-500" title={`${uploadedFileNames} · ${selectedSheetNames.join(", ")}`}>
                {uploadedFileNames} · {selectedSheetNames.length ? selectedSheetNames.join(", ") : "No sheets selected"}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setIsDataManagerOpen(true)}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            Manage data
            <ChevronDown className="h-4 w-4" />
          </button>
        </div>
      ) : (
      <div className="sm-panel px-4 py-4 sm:px-5">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm sm:text-[16px] font-semibold text-slate-900">
            Excel files
          </div>
          <div className="flex items-center gap-2">
            {uploadedFiles.length > 0 && (
              <button
                type="button"
                onClick={() => setIsDataManagerOpen(false)}
                title="Collapse data manager"
                aria-label="Collapse data manager"
                className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-200 text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                <ChevronUp className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          accept=".xlsx,.xls"
          hidden
          onChange={handleFileInputChange}
          multiple
          disabled={!isProjectReady || hasReachedLimit || isUploading}
        />

        {uploadedFiles.length === 0 && (
        <div
          className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 text-center transition-colors sm:p-8 ${
            isDragging ? "border-blue-400 bg-blue-50" : "border-slate-300 bg-slate-50/50"
          }`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          role="presentation"
        >
          <FileSpreadsheet className="w-7 h-7 sm:w-8 sm:h-8 text-green-500 mb-3" />
          <div className="text-xs sm:text-sm font-medium text-slate-500">
            Drop Excel files here
          </div>
          <p className="text-[11px] sm:text-xs text-slate-400">
            or click to browse (.xlsx, .xls files)
          </p>
          <p className="mt-1 text-[11px] sm:text-xs text-slate-400">
            Your original Excel files stay unchanged.
          </p>

          <button
            type="button"
            onClick={handleBrowseClick}
            disabled={!isProjectReady || hasReachedLimit || isUploading}
            className="mt-4 w-full sm:w-auto px-4 py-2 sm:px-5 sm:py-2.5 bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white text-sm sm:text-base font-medium rounded-md transition-colors inline-flex items-center justify-center"
          >
            <Upload className="w-4 h-4 mr-2" />
            {!isProjectReady ? "Loading..." : isUploading ? "Processing..." : "Browse Files"}
          </button>

          {helperText && (
            <div className="mt-4 text-xs sm:text-sm text-slate-500">{helperText}</div>
          )}
        </div>
        )}

        {uploadedFiles.length > 0 && (
          <>
            <div
              className={`mb-4 flex min-h-14 items-center justify-between gap-3 rounded-md border border-dashed px-3 py-2.5 transition-colors sm:px-4 ${
                hasReachedLimit
                  ? "border-slate-200 bg-slate-50 text-slate-400"
                  : isDragging
                    ? "border-blue-400 bg-blue-50 text-blue-700"
                    : "border-slate-300 bg-slate-50/50 text-slate-600"
              }`}
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              role="presentation"
            >
              <div className="flex min-w-0 items-center gap-2.5">
                <Upload className="h-4 w-4 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-medium">
                    {hasReachedLimit ? `Maximum of ${MAX_FILES} files reached` : "Drop another Excel file here"}
                  </p>
                  {!hasReachedLimit && (
                    <p className="text-xs text-slate-400">
                      .xlsx or .xls · Original files stay unchanged
                    </p>
                  )}
                </div>
              </div>
              <button
                type="button"
                onClick={handleBrowseClick}
                disabled={!isProjectReady || hasReachedLimit || isUploading}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-blue-600 transition-colors hover:border-blue-300 hover:bg-blue-50 disabled:cursor-not-allowed disabled:text-slate-400"
              >
                <Plus className="h-4 w-4" />
                {isUploading ? "Uploading..." : "Add files"}
              </button>
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase text-slate-500">
                Uploaded Files ({uploadedFiles.length})
              </p>

              <div className="divide-y divide-slate-100">
                {uploadedFiles.map((file) => (
                  <FileCard
                    key={file.id}
                    file={file}
                    projectId={activeProjectId}
                    onDelete={handleDeleteFile}
                    onUpdateSelectedSheets={handleSelectedSheetsUpdate}
                    onTogglePanel={handleToggleSheetPanel}
                    onShowToast={onShowToast}
                    onShowErrorModal={onShowErrorModal}
                  />
                ))}
              </div>
            </div>
          </>
        )}
      </div>
      )}
    </div>
  );
}

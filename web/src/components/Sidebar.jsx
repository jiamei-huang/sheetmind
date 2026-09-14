import React, { useState, useRef, useEffect } from "react";
import { FolderPlus, ChevronDown, ChevronRight, MoreVertical, Plus, X } from "lucide-react";
import TaskListPanel from "./AIDataAnalysis/TaskListPanel";

const Sidebar = ({
  projects = [],
  activeProjectId,
  onSelectProject,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
  tasks = [],
  activeTaskId,
  onSelectTask,
  onCreateTask,
  onRenameTask,
  onDeleteTask,
  isOpen = false,
  onClose,
}) => {
  const [isProjectListExpanded, setIsProjectListExpanded] = useState(true);
  const [openProjectMenuId, setOpenProjectMenuId] = useState(null);
  const [isRenameProjectModalOpen, setIsRenameProjectModalOpen] = useState(false);
  const [renameProjectId, setRenameProjectId] = useState(null);
  const [renameProjectName, setRenameProjectName] = useState("");
  const [isDeleteProjectModalOpen, setIsDeleteProjectModalOpen] = useState(false);
  const [deleteProjectId, setDeleteProjectId] = useState(null);
  const projectMenuRefs = useRef({});

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (openProjectMenuId && projectMenuRefs.current[openProjectMenuId]) {
        if (!projectMenuRefs.current[openProjectMenuId].contains(event.target)) {
          setOpenProjectMenuId(null);
        }
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [openProjectMenuId]);

  const handleRenameProject = (projectId) => {
    const project = projects.find((p) => p.id === projectId);
    if (project) {
      setRenameProjectId(projectId);
      setRenameProjectName(project.name);
      setIsRenameProjectModalOpen(true);
      setOpenProjectMenuId(null);
    }
  };

  const handleConfirmRenameProject = () => {
    if (renameProjectId && renameProjectName.trim() && onRenameProject) {
      onRenameProject(renameProjectId, renameProjectName.trim());
      setIsRenameProjectModalOpen(false);
      setRenameProjectId(null);
      setRenameProjectName("");
    }
  };

  const handleDeleteProject = (projectId) => {
    setDeleteProjectId(projectId);
    setIsDeleteProjectModalOpen(true);
    setOpenProjectMenuId(null);
  };

  const handleConfirmDeleteProject = () => {
    if (deleteProjectId && onDeleteProject) {
      onDeleteProject(deleteProjectId);
      setIsDeleteProjectModalOpen(false);
      setDeleteProjectId(null);
    }
  };

  return (
    <>
      {isOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="fixed inset-0 top-[60px] z-30 bg-slate-900/30 lg:hidden"
        />
      )}
      <div className={`${isOpen ? "flex" : "hidden"} fixed bottom-0 left-0 top-[60px] z-40 w-72 flex-col border-r border-slate-200 bg-white lg:flex`}>
      {/* Project Switcher */}
      <div className="border-b border-slate-200">
        <div className="px-4 py-3">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-slate-900">Project Switcher</h2>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setIsProjectListExpanded(!isProjectListExpanded)}
                className="p-1 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded transition-colors"
                aria-label={isProjectListExpanded ? "Collapse" : "Expand"}
              >
                {isProjectListExpanded ? (
                  <ChevronDown className="w-4 h-4" />
                ) : (
                  <ChevronRight className="w-4 h-4" />
                )}
              </button>
              <button
                type="button"
                onClick={onClose}
                aria-label="Close navigation panel"
                className="inline-flex h-7 w-7 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-800 lg:hidden"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          <button
            onClick={() => {
              onCreateProject();
              onClose?.();
            }}
            className="mb-3 flex w-full items-center justify-center gap-2 rounded-md bg-slate-100 px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            aria-label="Create new project"
          >
            <FolderPlus className="w-4 h-4" />
            New Project
          </button>

          {isProjectListExpanded && (
            <div className="space-y-1">
              {projects.map((project) => {
                const isActive = project.id === activeProjectId;
                return (
                  <div
                    key={project.id}
                    className={`flex items-center justify-between rounded transition-colors ${
                      isActive
                        ? "bg-blue-50 text-blue-700"
                        : "text-slate-700 hover:bg-slate-50"
                    }`}
                  >
                    <button
                      onClick={() => {
                        onSelectProject(project.id);
                        onClose?.();
                      }}
                      className={`flex-1 px-3 py-2 text-left text-sm font-medium ${
                        isActive ? "text-blue-700" : "text-slate-700"
                      }`}
                    >
                      {project.name}
                    </button>
                    <div className="relative flex-shrink-0" ref={(el) => (projectMenuRefs.current[project.id] = el)}>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenProjectMenuId(openProjectMenuId === project.id ? null : project.id);
                        }}
                        className="p-1 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded transition-colors mr-2"
                        aria-label="Project options"
                      >
                        <MoreVertical className="w-4 h-4" />
                      </button>
                      {openProjectMenuId === project.id && (
                        <div className="absolute right-0 mt-1 w-32 bg-white border border-slate-200 rounded-md shadow-lg z-10">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRenameProject(project.id);
                            }}
                            className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-100"
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteProject(project.id);
                            }}
                            className="w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-slate-100"
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Task List */}
      <div className="flex-1 overflow-hidden flex flex-col min-h-0">
        <div className="flex-shrink-0 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-900 mb-3">Task List</h2>
          <button
            onClick={() => {
              onCreateTask();
              onClose?.();
            }}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            aria-label="Create new task"
          >
            <Plus className="h-4 w-4" />
            New Task
          </button>
        </div>
        <TaskListPanel
          tasks={tasks}
          activeTaskId={activeTaskId}
          onSelectTask={(taskId) => {
            onSelectTask(taskId);
            onClose?.();
          }}
          onRenameTask={onRenameTask}
          onDeleteTask={onDeleteTask}
        />
      </div>

      {/* Rename Project Modal */}
      {isRenameProjectModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="sm-dialog w-full max-w-sm space-y-4 p-6" role="dialog" aria-modal="true" aria-labelledby="rename-project-title">
            <div>
              <h4 id="rename-project-title" className="text-base font-semibold text-slate-900">Rename Project</h4>
              <p className="mt-1 text-sm text-slate-600">Enter a new name for this project.</p>
            </div>
            <div>
              <input
                type="text"
                value={renameProjectName}
                onChange={(e) => setRenameProjectName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    handleConfirmRenameProject();
                  } else if (e.key === "Escape") {
                    setIsRenameProjectModalOpen(false);
                    setRenameProjectName("");
                  }
                }}
                className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="Project name"
                autoFocus
              />
            </div>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setIsRenameProjectModalOpen(false);
                  setRenameProjectName("");
                  setRenameProjectId(null);
                }}
                className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-md hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmRenameProject}
                disabled={!renameProjectName.trim()}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed rounded-md transition-colors"
              >
                Rename
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Project Modal */}
      {isDeleteProjectModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="sm-dialog w-full max-w-sm space-y-4 p-6" role="alertdialog" aria-modal="true" aria-labelledby="delete-project-title">
            <div>
              <h4 id="delete-project-title" className="text-base font-semibold text-slate-900">Delete Project</h4>
              <p className="mt-1 text-sm text-slate-600">
                Are you sure you want to delete this project? This action cannot be undone.
                {deleteProjectId && (
                  <>
                    {" "}
                    (Project: {projects.find((p) => p.id === deleteProjectId)?.name ?? ""})
                  </>
                )}
              </p>
            </div>
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setIsDeleteProjectModalOpen(false);
                  setDeleteProjectId(null);
                }}
                className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-200 rounded-md hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmDeleteProject}
                className="px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-md transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
      </div>
    </>
  );
};

export default Sidebar;

import React, { useState, useRef, useEffect } from "react";
import { FolderPlus, ChevronDown, ChevronRight, MoreVertical } from "lucide-react";
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
    <div className="fixed left-0 top-[60px] bottom-[80px] w-72 bg-white border-r border-gray-200 flex flex-col z-40">
      {/* Project Switcher */}
      <div className="border-b border-gray-200">
        <div className="px-4 py-3">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-900">Project Switcher</h2>
            <button
              onClick={() => setIsProjectListExpanded(!isProjectListExpanded)}
              className="p-1 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors"
              aria-label={isProjectListExpanded ? "Collapse" : "Expand"}
            >
              {isProjectListExpanded ? (
                <ChevronDown className="w-4 h-4" />
              ) : (
                <ChevronRight className="w-4 h-4" />
              )}
            </button>
          </div>

          <button
            onClick={onCreateProject}
            className="w-full px-3 py-2 bg-gray-100 hover:bg-gray-200 hover:shadow-sm text-gray-700 rounded transition-colors font-medium text-sm flex items-center justify-center gap-2 mb-3"
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
                        : "text-gray-700 hover:bg-gray-50 hover:shadow-sm"
                    }`}
                  >
                    <button
                      onClick={() => onSelectProject(project.id)}
                      className={`flex-1 px-3 py-2 text-left text-sm font-medium ${
                        isActive ? "text-blue-700" : "text-gray-700"
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
                        className="p-1 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors mr-2"
                        aria-label="Project options"
                      >
                        <MoreVertical className="w-4 h-4" />
                      </button>
                      {openProjectMenuId === project.id && (
                        <div className="absolute right-0 mt-1 w-32 bg-white border border-gray-200 rounded-md shadow-lg z-10">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleRenameProject(project.id);
                            }}
                            className="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100"
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteProject(project.id);
                            }}
                            className="w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-gray-100"
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
        <div className="px-4 py-3 border-b border-gray-200 flex-shrink-0">
          <h2 className="text-sm font-semibold text-gray-900 mb-3">Task List</h2>
          <button
            onClick={onCreateTask}
            className="w-full px-3 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 hover:shadow-sm transition-colors font-medium text-sm flex items-center justify-center gap-2"
            aria-label="Create new task"
          >
            <span>+</span>
            New Task
          </button>
        </div>
        <TaskListPanel
          tasks={tasks}
          activeTaskId={activeTaskId}
          onSelectTask={onSelectTask}
          onRenameTask={onRenameTask}
          onDeleteTask={onDeleteTask}
        />
      </div>

      {/* Rename Project Modal */}
      {isRenameProjectModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
          <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6 space-y-4">
            <div>
              <h4 className="text-base font-semibold text-gray-900">Rename Project</h4>
              <p className="mt-1 text-sm text-gray-600">Enter a new name for this project.</p>
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
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
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
                className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-md hover:bg-gray-100 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmRenameProject}
                disabled={!renameProjectName.trim()}
                className="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed rounded-md transition-colors"
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
          <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6 space-y-4">
            <div>
              <h4 className="text-base font-semibold text-gray-900">Delete Project</h4>
              <p className="mt-1 text-sm text-gray-600">
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
                className="px-4 py-2 text-sm font-medium text-gray-600 border border-gray-200 rounded-md hover:bg-gray-100 transition-colors"
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
  );
};

export default Sidebar;

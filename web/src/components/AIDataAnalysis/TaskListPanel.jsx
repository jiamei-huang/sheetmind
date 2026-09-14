import React, { useState, useRef, useEffect } from "react";
import { MoreVertical } from "lucide-react";

const TaskListPanel = ({ tasks, activeTaskId, onSelectTask, onRenameTask, onDeleteTask }) => {
  const [openMenuId, setOpenMenuId] = useState(null);
  const menuRefs = useRef({});
  const sortedTasks = [...tasks].sort((a, b) => {
    const timeA = a.createdAt || 0;
    const timeB = b.createdAt || 0;
    return timeB - timeA;
  });

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (openMenuId && menuRefs.current[openMenuId]) {
        if (!menuRefs.current[openMenuId].contains(event.target)) {
          setOpenMenuId(null);
        }
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [openMenuId]);

  return (
    <div className="flex-1 space-y-1 overflow-y-auto bg-white px-3 pb-4">
        {sortedTasks.length === 0 ? (
          <div className="text-center text-slate-500 text-sm py-8">
            No tasks yet. Create one to get started.
          </div>
        ) : (
          sortedTasks.map((task) => {
            const isActive = task.id === activeTaskId;
            const statusMeta = {
              draft: { dot: "bg-amber-400", label: "Draft" },
              running: { dot: "bg-blue-500 animate-pulse", label: "Running" },
              completed: { dot: "bg-emerald-500", label: "Completed" },
            }[task.status] || { dot: "bg-amber-400", label: "Draft" };

            return (
              <div
                key={task.id}
                className={`group relative rounded-md transition-colors ${
                  isActive ? "bg-blue-50" : "hover:bg-slate-50"
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSelectTask?.(task.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={`flex min-h-[64px] w-full flex-col justify-center border-l-2 px-3 py-2 pr-11 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${
                    isActive ? "border-blue-600" : "border-transparent"
                  }`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${statusMeta.dot}`} aria-hidden="true" />
                    <span className="truncate text-sm font-semibold text-slate-900">
                      {task.title || `Task #${task.number}`}
                    </span>
                  </span>
                  <span className="mt-1 pl-4 text-xs text-slate-500">
                    {new Date(task.createdAt).toLocaleString()}
                  </span>
                  <span className="sr-only">{statusMeta.label}</span>
                </button>
                <div className="absolute right-2 top-2.5" ref={(el) => (menuRefs.current[task.id] = el)}>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuId(openMenuId === task.id ? null : task.id);
                        }}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-white hover:text-slate-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                        aria-label="Task options"
                      >
                        <MoreVertical className="w-4 h-4" />
                      </button>
                      {openMenuId === task.id && (
                        <div className="absolute right-0 top-9 z-10 w-32 rounded-md border border-slate-200 bg-white shadow-lg">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuId(null);
                              onRenameTask(task.id);
                            }}
                            className="w-full px-3 py-2 text-left text-sm text-slate-700 hover:bg-slate-100"
                          >
                            Rename
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuId(null);
                              onDeleteTask(task.id);
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
          })
        )}
    </div>
  );
};

export default TaskListPanel;

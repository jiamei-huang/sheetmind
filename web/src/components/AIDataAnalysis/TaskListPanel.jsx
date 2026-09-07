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
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2 bg-gray-50">
        {sortedTasks.length === 0 ? (
          <div className="text-center text-gray-500 text-sm py-8">
            No tasks yet. Create one to get started.
          </div>
        ) : (
          sortedTasks.map((task) => {
            const isActive = task.id === activeTaskId;
            const statusMeta = {
              draft: { icon: "🟡", label: "Draft" },
              running: { icon: "⏳", label: "Running" },
              completed: { icon: "✅", label: "Completed" },
            }[task.status] || { icon: "🟡", label: "Draft" };

            return (
              <div
                key={task.id}
                className={`bg-white rounded border-2 transition-all ${
                  isActive ? "border-blue-500 shadow-md" : "border-gray-200 hover:border-gray-300 hover:shadow-sm"
                }`}
              >
                <div className="p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div
                      className="flex items-center gap-2 flex-1 min-w-0 cursor-pointer"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (onSelectTask) {
                          onSelectTask(task.id);
                        }
                      }}
                    >
                      <span className="text-sm">{statusMeta.icon}</span>
                      <span className="text-sm font-semibold text-gray-900 truncate">
                        {task.title || `Task #${task.number}`}
                      </span>
                    </div>
                    <div className="relative flex-shrink-0" ref={(el) => (menuRefs.current[task.id] = el)}>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenMenuId(openMenuId === task.id ? null : task.id);
                        }}
                        className="p-1 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors mr-2"
                        aria-label="Task options"
                      >
                        <MoreVertical className="w-4 h-4" />
                      </button>
                      {openMenuId === task.id && (
                        <div className="absolute right-0 mt-1 w-32 bg-white border border-gray-200 rounded-md shadow-lg z-10">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setOpenMenuId(null);
                              onRenameTask(task.id);
                            }}
                            className="w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100"
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
                            className="w-full px-3 py-2 text-left text-sm text-red-600 hover:bg-gray-100"
                          >
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                  <div
                    onClick={(e) => {
                      e.stopPropagation();
                      if (onSelectTask) {
                        onSelectTask(task.id);
                      }
                    }}
                    className="cursor-pointer"
                  >
                    <p className="text-xs" style={{ color: "#888" }}>
                      {new Date(task.createdAt).toLocaleString()}
                    </p>
                  </div>
                </div>
              </div>
            );
          })
        )}
    </div>
  );
};

export default TaskListPanel;

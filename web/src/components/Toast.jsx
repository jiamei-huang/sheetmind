import React, { useEffect } from "react";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";

const toastStyles = {
  success: {
    container: "bg-green-50 border-green-200",
    icon: "text-green-600",
    title: "text-green-800",
    desc: "text-green-700",
  },
  error: {
    container: "bg-red-50 border-red-200",
    icon: "text-red-600",
    title: "text-red-800",
    desc: "text-red-700",
  },
  info: {
    container: "bg-blue-50 border-blue-200",
    icon: "text-blue-600",
    title: "text-blue-800",
    desc: "text-blue-700",
  },
};

const toastIcons = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};

/**
 * Toast 轻量提示组件
 * 用于显示自动关闭的轻量提示（成功、信息等）
 *
 * @param {string} message - 提示消息（支持标题+描述格式，用 "|" 分隔，如 "标题|描述"）
 * @param {string} title - 标题（可选，如果提供则与 message 一起显示）
 * @param {string} type - 类型：success, info, error
 * @param {function} onDismiss - 关闭回调
 * @param {number} duration - 自动关闭时间（毫秒），默认3000ms
 * @param {boolean} autoClose - 是否自动关闭，默认true
 */
export default function Toast({
  message,
  title,
  type = "info",
  onDismiss,
  duration = 3000,
  autoClose = true
}) {
  useEffect(() => {
    if (!message || !autoClose) return undefined;

    const timer = setTimeout(() => {
      onDismiss?.();
    }, duration);

    return () => clearTimeout(timer);
  }, [duration, message, onDismiss, autoClose]);

  if (!message) {
    return null;
  }

  // 解析消息：支持 "标题|描述" 格式，或直接使用 message
  let displayTitle = title;
  let displayDesc = message;

  // 如果没有提供 title，尝试从 message 中解析（使用 "|" 分隔）
  if (!displayTitle && message.includes("|")) {
    const parts = message.split("|");
    displayTitle = parts[0].trim();
    displayDesc = parts.slice(1).join("|").trim();
  }

  const style = toastStyles[type] ?? toastStyles.info;
  const IconComponent = toastIcons[type] ?? Info;

  return (
    <div className="fixed top-4 right-4 z-50">
      <div
        className={`min-w-[280px] max-w-sm border rounded-lg shadow-lg px-4 py-3 transition-all ${
          style.container
        }`}
      >
        <div className="flex gap-3">
          {/* 图标 - 与标题对齐 */}
          <div className="flex-shrink-0 flex items-start pt-0.5">
            <IconComponent className={`w-5 h-5 ${style.icon}`} />
          </div>

          {/* 文字内容 */}
          <div className="flex-1 min-w-0">
            {displayTitle ? (
              <div>
                <div className={`text-sm font-medium leading-5 ${style.title}`}>
                  {displayTitle}
                </div>
                {displayDesc && (
                  <div className={`text-xs ${style.desc} line-clamp-2 leading-4 mt-0.5`}>
                    {displayDesc}
                  </div>
                )}
              </div>
            ) : (
              <div className={`text-sm ${style.desc} leading-5`}>
                {displayDesc}
              </div>
            )}
          </div>

          {/* 关闭按钮 */}
          <button
            type="button"
            aria-label="关闭提示"
            onClick={onDismiss}
            className="flex-shrink-0 text-slate-400 hover:text-slate-600 transition-colors p-1 -mr-1"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

import React from "react";
import { AlertCircle } from "lucide-react";

/**
 * 重要错误弹窗组件
 * 用于显示需要用户手动关闭的重要错误信息（如接口请求失败、上传失败等）
 */
export default function ErrorModal({ message, onClose, title = "错误" }) {
  if (!message) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 px-4">
      <div className="sm-dialog w-full max-w-md space-y-4 p-6" role="alertdialog" aria-modal="true" aria-labelledby="error-dialog-title">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0">
            <AlertCircle className="w-6 h-6 text-red-500" />
          </div>
          <div className="flex-1">
            <h3 id="error-dialog-title" className="mb-2 text-lg font-semibold text-slate-900">{title}</h3>
            <p className="text-sm text-slate-600 whitespace-pre-wrap">{message}</p>
          </div>
        </div>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium rounded-md transition-colors"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  );
}

import { BASE_URL } from "./client";

const STREAM_TIMEOUT_MS = 300000;

const readError = async (response) => {
  const text = await response.text().catch(() => "");
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
};

export const toAnalysisErrorMessage = (error) => {
  const rawMessage = String(
    error?.response?.data?.detail ?? error?.detail ?? error?.message ?? ""
  );
  const lower = rawMessage.toLowerCase();

  if (!rawMessage || lower.includes("failed to fetch") || lower.includes("network")) {
    return "无法连接后端服务。请确认已在 server 目录运行 `python3 -m sheetmind`。";
  }
  if (error?.name === "AbortError" || lower.includes("timeout") || lower.includes("timed out")) {
    return "分析耗时过长，已停止等待。可以缩小 Sheet 范围或拆分问题后重试。";
  }
  if (lower.includes("api key") || lower.includes("unauthorized")) {
    return "模型服务尚未正确配置，请检查 server/.env。";
  }
  if (lower.includes("task") && lower.includes("not found")) {
    return "当前分析任务不存在，请新建任务后重试。";
  }
  if (lower.includes("column") || lower.includes("keyerror") || rawMessage.includes("列")) {
    return "没有匹配到问题里的字段，请尝试使用 Excel 中的原始列名。";
  }
  if (lower.includes("stream ended")) {
    return "分析连接提前中断，请重新提交问题。";
  }
  return rawMessage;
};

export const analyzeStream = ({ taskId, query, selectedFiles = [], onEvent }) =>
  new Promise(async (resolve, reject) => {
    if (!taskId || !query?.trim()) {
      reject(new Error("Task ID and query are required"));
      return;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);
    try {
      const response = await fetch(`${BASE_URL}/analysis/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ taskId, query: query.trim(), selectedFiles }),
        signal: controller.signal,
      });
      if (!response.ok) {
        reject(new Error(toAnalysisErrorMessage(await readError(response))));
        return;
      }
      if (!response.body) {
        reject(new Error("浏览器没有收到流式响应体。"));
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
          if (!dataLine) continue;
          let event;
          try {
            event = JSON.parse(dataLine.slice(6));
          } catch {
            continue;
          }
          onEvent?.(event);
          if (event.event === "done") {
            resolve(event.result);
            return;
          }
          if (event.event === "error") {
            reject(new Error(toAnalysisErrorMessage(event)));
            return;
          }
        }
      }
      reject(new Error("Stream ended without a done event"));
    } catch (error) {
      reject(new Error(toAnalysisErrorMessage(error)));
    } finally {
      window.clearTimeout(timeoutId);
    }
  });

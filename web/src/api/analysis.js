import { BASE_URL } from "./client.js";

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
    return "Could not connect to the backend service. Make sure `python3 -m sheetmind` is running in the server directory.";
  }
  if (error?.name === "AbortError" || lower.includes("timeout") || lower.includes("timed out")) {
    return "The analysis took too long and was stopped. Try selecting fewer sheets or splitting the question.";
  }
  if (lower.includes("api key") || lower.includes("unauthorized")) {
    return "The model service is not configured correctly. Check server/.env.";
  }
  if (lower.includes("task") && lower.includes("not found")) {
    return "This analysis task no longer exists. Create a new task and try again.";
  }
  if (lower.includes("column") || lower.includes("keyerror") || rawMessage.includes("列")) {
    return "No matching field was found. Try using the original column name from the Excel file.";
  }
  if (lower.includes("stream ended")) {
    return "The analysis connection ended early. Submit the question again.";
  }
  return rawMessage;
};

export const buildSelectedFileScope = (files = []) =>
  files.flatMap((file) => {
    const fileName = file?.fileName || file?.name;
    const fileId = file?.fileId;
    const sheets = Array.isArray(file?.selectedSheets)
      ? file.selectedSheets.filter(Boolean)
      : [];
    return fileName && sheets.length ? [{ fileId, fileName, sheets }] : [];
  });

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
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ taskId, query: query.trim(), selectedFiles }),
        signal: controller.signal,
      });
      if (!response.ok) {
        reject(new Error(toAnalysisErrorMessage(await readError(response))));
        return;
      }
      if (!response.body) {
        reject(new Error("The browser did not receive a streaming response body."));
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


export const downloadArtifactExcel = async (artifactId, taskId) => {
  const response = await fetch(
    `${BASE_URL}/analysis/artifacts/${encodeURIComponent(artifactId)}/excel?taskId=${encodeURIComponent(taskId)}`,
    { credentials: "include" }
  );
  if (!response.ok) {
    throw new Error(toAnalysisErrorMessage(await readError(response)));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `analysis-${artifactId.slice(0, 8)}.xlsx`;
  link.click();
  URL.revokeObjectURL(url);
};

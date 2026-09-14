import apiClient from "./client.js";
import { toAnalysisViewModel } from "./resultBlocks.js";


export const getTaskConversation = async (taskId) => {
  const response = await apiClient.get(`/conversations/${taskId}`);
  return response.data.messages ?? [];
};


const legacyTextResult = (prompt, message) => {
  const failed = message.metadata?.status === "failed";
  return {
  type: failed ? "error" : "insight_only",
  outputIntents: ["text"],
  mode: failed ? "error" : "insight",
  classification: failed ? "Analysis Failed" : "Insight",
  prompt,
  processedAt: message.createdAt ?? new Date().toISOString(),
  suggestion: failed ? "" : message.content ?? "",
  status: failed ? "failed" : "success",
  statusMessages: failed
    ? [{ kind: "status", status: "failed", message: message.content ?? "Analysis failed." }]
    : [],
  summaryItems: [],
  questionResults: [],
  metrics: [],
  fieldResolutions: [],
  sheetResolutions: [],
  runtimeNote: "",
  previews: [],
  preview: null,
  chartDatas: [],
  chartData: null,
  };
};


export const conversationHistoryToResults = (messages = []) => {
  const results = [];
  let pendingUser = null;
  const usersByRunId = new Map();

  for (const message of messages) {
    if (message?.role === "user") {
      const runId = message.metadata?.runId;
      if (runId) {
        usersByRunId.set(runId, message);
      }
      pendingUser = message;
      continue;
    }
    if (message?.role !== "assistant") {
      continue;
    }

    const runId = message.metadata?.runId;
    const matchedUser = (runId && usersByRunId.get(runId)) || pendingUser;
    const prompt = matchedUser?.content ?? "";
    const payload = message.metadata?.result;
    if (payload?.type === "result_blocks" && Array.isArray(payload.blocks)) {
      try {
        results.push({
          ...toAnalysisViewModel(payload, prompt),
          processedAt: message.createdAt ?? new Date().toISOString(),
        });
      } catch {
        results.push(legacyTextResult(prompt, message));
      }
    } else {
      results.push(legacyTextResult(prompt, message));
    }
    pendingUser = null;
    if (runId) usersByRunId.delete(runId);
  }

  return results;
};

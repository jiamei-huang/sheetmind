import apiClient from "./client.js";


const APP_STORAGE_KEYS = new Set(["sheetmind_active_project"]);
const APP_STORAGE_PREFIXES = ["excel_", "tasks_", "lastActiveTask_"];


export const getAnonymousSession = async () => {
  const response = await apiClient.get("/session");
  return response.data;
};


export const resetAnonymousSession = async () => {
  const response = await apiClient.delete("/session");
  return response.data;
};


export const clearAnonymousBrowserState = (storage = window.localStorage) => {
  const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
  keys
    .filter(
      (key) =>
        key &&
        (APP_STORAGE_KEYS.has(key) || APP_STORAGE_PREFIXES.some((prefix) => key.startsWith(prefix)))
    )
    .forEach((key) => storage.removeItem(key));
};

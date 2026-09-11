import axios from "axios";

const browserHost = typeof window !== "undefined" ? window.location.hostname : "127.0.0.1";
const BACKEND_HOST = import.meta.env.VITE_API_BASE_URL || `http://${browserHost}:8000`;
const API_PREFIX = "/api";
const BASE_URL = `${BACKEND_HOST}${API_PREFIX}`;

const apiClient = axios.create({
  baseURL: BASE_URL,
  timeout: 300000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url || "Unknown";
    console.error(`[API Error] ${error.response?.status || "Network"} ${url}`, error.message);
    return Promise.reject(error);
  }
);

export default apiClient;
export { BASE_URL, BACKEND_HOST };

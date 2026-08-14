"use client";
import { useEffect } from "react";

import { API_BASE } from "@/lib/api";

/**
 * Silently pings the backend /health endpoint on every page load
 * so Render's free tier wakes up before the user makes real API calls.
 */
export default function BackendWakeup() {
  useEffect(() => {
    const api = API_BASE;
    fetch(`${api}/health`, { method: "GET" }).catch(() => {/* ignore */});
  }, []);
  return null;
}

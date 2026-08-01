"use client";
import { useEffect } from "react";

/**
 * Silently pings the backend /health endpoint on every page load
 * so Render's free tier wakes up before the user makes real API calls.
 */
export default function BackendWakeup() {
  useEffect(() => {
    const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    fetch(`${api}/health`, { method: "GET" }).catch(() => {/* ignore */});
  }, []);
  return null;
}

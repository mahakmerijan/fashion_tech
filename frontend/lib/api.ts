// Render's fromService.host gives a bare hostname (e.g. "my-svc-xyz")
// without ".onrender.com". This normalises all possible formats to a full URL.
const _rawApiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
let API_BASE: string;
if (_rawApiUrl.startsWith("http")) {
  API_BASE = _rawApiUrl;                                   // already full URL
} else if (_rawApiUrl.includes(".")) {
  API_BASE = `https://${_rawApiUrl}`;                      // hostname.domain
} else {
  API_BASE = `https://${_rawApiUrl}.onrender.com`;         // bare Render service name
}

// ── Auth ───────────────────────────────────────────────────────────────────

export async function registerUser(email: string, password: string) {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "Registration failed");
  return res.json() as Promise<{ user_id: string; token: string; email: string }>;
}

export async function loginUser(email: string, password: string) {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "Login failed");
  return res.json() as Promise<{ user_id: string; token: string; email: string }>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── Face Analysis ──────────────────────────────────────────────────────────

export async function analyzeFace(formData: FormData) {
  const res = await fetch(`${API_BASE}/api/face/analyze`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// ── User / Profile ─────────────────────────────────────────────────────────

export async function createProfile(data: unknown) {
  return request("/api/users/profile", { method: "POST", body: JSON.stringify(data) });
}

export async function getProfile(userId: string) {
  return request(`/api/users/profile/${userId}`);
}

export async function updatePreferences(userId: string, data: unknown) {
  return request(`/api/users/profile/${userId}/preferences`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

// ── Wardrobe ───────────────────────────────────────────────────────────────

export async function uploadWardrobeItems(userId: string, formData: FormData) {
  const res = await fetch(`${API_BASE}/api/wardrobe/${userId}/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getWardrobe(userId: string) {
  return request(`/api/wardrobe/${userId}`);
}

export async function deleteWardrobeItem(userId: string, itemId: string) {
  return request(`/api/wardrobe/${userId}/items/${itemId}`, { method: "DELETE" });
}

// ── Recommendations ────────────────────────────────────────────────────────

export async function getRecommendations(data: unknown) {
  return request("/api/recommendations", { method: "POST", body: JSON.stringify(data) });
}

// ── Image Generation ───────────────────────────────────────────────────────

export async function generateOutfitImage(data: unknown) {
  return request("/api/images/generate", { method: "POST", body: JSON.stringify(data) });
}

// ── Shopping ───────────────────────────────────────────────────────────────

export async function searchShopping(params: { category: string; color: string; fit: string }) {
  const q = new URLSearchParams(params).toString();
  return request(`/api/shopping/search?${q}`);
}

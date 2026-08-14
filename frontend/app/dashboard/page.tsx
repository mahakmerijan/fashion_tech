"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useUserStore } from "@/stores/user-store";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function resolveImageUrl(url: string): string {
  if (!url) return "";
  if (url.startsWith("data:")) return url;
  if (url.startsWith("http")) return url;
  return `${API}${url}`;
}

export default function DashboardPage() {
  const { userId, faceProfile, preferences, wardrobe, setFaceProfile } = useUserStore();
  const [reanalyzing, setReanalyzing] = useState(false);

  // Auto-retry face analysis if face_shape is blank (Gemini failed during onboarding)
  useEffect(() => {
    if (faceProfile && !faceProfile.face_shape && !reanalyzing) {
      const selfieB64 = typeof window !== "undefined" ? localStorage.getItem("selfie_b64") : null;
      if (!selfieB64) return;
      setReanalyzing(true);
      (async () => {
        try {
          const raw = selfieB64.split(",").pop() || selfieB64;
          const byteArr = Uint8Array.from(atob(raw), c => c.charCodeAt(0));
          const blob = new Blob([byteArr], { type: "image/jpeg" });
          const fd = new FormData();
          fd.append("image", blob, "selfie.jpg");
          if (userId) fd.append("user_id", userId);
          const res = await fetch(`${API}/api/face/analyze`, { method: "POST", body: fd });
          if (res.ok) {
            const data = await res.json();
            if (data.face_profile?.face_shape) {
              setFaceProfile(data.face_profile);
            }
          }
        } catch (e) {
          console.error("Re-analysis failed:", e);
        } finally {
          setReanalyzing(false);
        }
      })();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [faceProfile]);

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      {/* Top nav */}
      <nav className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between">
        <span className="font-bold text-xl text-violet-700">StyleAI ✨</span>
        <div className="flex gap-3">
          <Link href="/onboarding/wardrobe">
            <Button variant="outline" size="sm">Manage Wardrobe</Button>
          </Link>
          <Link href="/dashboard/occasion">
            <Button size="sm">Get Recommendations</Button>
          </Link>
        </div>
      </nav>

      <main className="max-w-5xl mx-auto px-6 py-10 space-y-8">
        <div className="page-enter">
          <h1 className="text-3xl font-bold text-slate-900 mb-1">Your Style Dashboard</h1>
          <p className="text-slate-500">Here&apos;s your personalised style profile</p>
        </div>

        {/* Face Profile card */}
        {faceProfile && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                🪞 Face Profile
                {reanalyzing && <span className="text-xs font-normal text-violet-500 animate-pulse ml-2">Re-analysing…</span>}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                {Object.entries(faceProfile)
                  .filter(([k]) => !k.includes("hex") && String(faceProfile[k as keyof typeof faceProfile]))
                  .map(([key, val]) => (
                    <div key={key} className="bg-violet-50 rounded-xl p-3">
                      <p className="text-xs text-slate-400 capitalize">{key.replace(/_/g, " ")}</p>
                      <p className="font-semibold text-slate-800 mt-0.5">{String(val)}</p>
                    </div>
                  ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Style Preferences card */}
        {preferences.fit && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">👔 Style Preferences</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                <div className="bg-slate-50 rounded-xl p-3">
                  <p className="text-xs text-slate-400">Preferred Fit</p>
                  <p className="font-semibold text-slate-800">{preferences.fit}</p>
                </div>
                <div className="bg-slate-50 rounded-xl p-3">
                  <p className="text-xs text-slate-400">Top Priority</p>
                  <p className="font-semibold text-slate-800">{preferences.priority}</p>
                </div>
                <div className="bg-slate-50 rounded-xl p-3">
                  <p className="text-xs text-slate-400">Experimentation</p>
                  <p className="font-semibold text-slate-800">{preferences.experiment_level} / 5</p>
                </div>
                {preferences.favorite_colors && (
                  <div className="bg-slate-50 rounded-xl p-3 col-span-2">
                    <p className="text-xs text-slate-400 mb-2">Favourite Colors</p>
                    <div className="flex flex-wrap gap-1.5">
                      {preferences.favorite_colors.map((c) => (
                        <Badge key={c} variant="secondary">{c}</Badge>
                      ))}
                    </div>
                  </div>
                )}
                {preferences.fabrics && (
                  <div className="bg-slate-50 rounded-xl p-3 col-span-2">
                    <p className="text-xs text-slate-400 mb-2">Preferred Fabrics</p>
                    <div className="flex flex-wrap gap-1.5">
                      {preferences.fabrics.map((f) => (
                        <Badge key={f} variant="secondary">{f}</Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Wardrobe summary */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>👗 My Wardrobe</span>
              <Badge variant="default">{wardrobe.length} items</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {wardrobe.length === 0 ? (
              <div className="text-center py-8 text-slate-400">
                <p className="text-4xl mb-3">📦</p>
                <p>No wardrobe items yet.</p>
                <Link href="/onboarding/wardrobe">
                  <Button variant="outline" size="sm" className="mt-3">Upload Clothes</Button>
                </Link>
              </div>
            ) : (
              <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
                {wardrobe.slice(0, 12).map((item) => (
                  <div key={item.item_id} className="rounded-xl overflow-hidden aspect-square border border-slate-200 bg-slate-100">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={resolveImageUrl(item.image_url)}
                      alt={item.category}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        (e.target as HTMLImageElement).src = `https://placehold.co/120x120/e0d7ff/7c3aed?text=${encodeURIComponent(item.category || "?")}`;
                      }}
                    />
                  </div>
                ))}
                {wardrobe.length > 12 && (
                  <div className="rounded-xl aspect-square border border-slate-200 bg-slate-50 flex items-center justify-center text-sm text-slate-400">
                    +{wardrobe.length - 12}
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        {/* CTA */}
        <div className="gradient-hero rounded-3xl p-8 text-white text-center">
          <h2 className="text-2xl font-bold mb-2">Ready to get dressed?</h2>
          <p className="text-white/80 mb-6">Tell us the occasion and we&apos;ll curate the perfect outfit from your wardrobe</p>
          <Link href="/dashboard/occasion">
            <Button size="lg" className="bg-white text-violet-700 hover:bg-white/90 font-semibold">
              Get Outfit Recommendations ✨
            </Button>
          </Link>
        </div>
      </main>
    </div>
  );
}

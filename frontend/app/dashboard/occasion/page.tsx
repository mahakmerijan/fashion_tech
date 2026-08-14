"use client";
import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { useUserStore } from "@/stores/user-store";

import { API_BASE } from "@/lib/api";

export default function SituationPage() {
  const router = useRouter();
  const { userId, faceProfile, preferences, wardrobe } = useUserStore();

  const [situationText, setSituationText] = useState("");
  const [personDesc, setPersonDesc] = useState("");
  const [placeFile, setPlaceFile] = useState<File | null>(null);
  const [placePreview, setPlacePrev] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback((file: File) => {
    if (!file.type.startsWith("image/")) return;
    setPlaceFile(file);
    // Generate a preview blob URL for display only
    setPlacePrev(URL.createObjectURL(file));
    // Also convert to compressed base64 for sessionStorage (survives reloads)
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target?.result as string;
      // Compress via canvas before storing
      const img = new Image();
      img.onload = () => {
        const MAX = 800;
        const scale = Math.min(1, MAX / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = img.width * scale;
        canvas.height = img.height * scale;
        canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
        sessionStorage.setItem("place_image_b64", canvas.toDataURL("image/jpeg", 0.75));
      };
      img.src = dataUrl;
    };
    reader.readAsDataURL(file);
  }, []);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  };

  const handleSubmit = async () => {
    if (!situationText.trim()) return;
    setLoading(true);
    try {
      const API = API_BASE;
      const fd = new FormData();
      fd.append("situation_text", situationText);
      fd.append("person_description", personDesc);
      fd.append("user_id", userId || "");
      fd.append("face_profile", JSON.stringify(faceProfile || {}));
      fd.append("preferences", JSON.stringify(preferences || {}));
      fd.append("wardrobe_ids", JSON.stringify(wardrobe.map((w) => w.item_id)));
      fd.append("wardrobe_meta", JSON.stringify(wardrobe));
      // Send selfie so backend can preserve user's face in generated image
      const selfieB64 = localStorage.getItem("selfie_b64") || "";
      fd.append("selfie_b64", selfieB64);
      if (placeFile) fd.append("place_image", placeFile);

      const res = await fetch(`${API}/api/situation/recommend`, {
        method: "POST",
        body: fd,
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();

      // Store result + place image in session (use base64 so it survives reloads)
      sessionStorage.setItem("situation_result", JSON.stringify(data));
      sessionStorage.setItem("situation_text", situationText);
      sessionStorage.setItem("person_desc", personDesc);
      // Store base64 preview if available, otherwise clear
      if (!sessionStorage.getItem("place_image_b64") && placePreview) {
        sessionStorage.setItem("place_preview", placePreview);
      }

      router.push("/dashboard/recommendations");
    } catch (err) {
      console.error(err);
      setLoading(false);
    }
  };

  const canSubmit = situationText.trim().length > 10 && !loading;

  return (
    <div className="min-h-screen bg-gradient-to-br from-violet-50 to-indigo-100 flex flex-col items-center py-10 px-4">
      <div className="w-full max-w-2xl space-y-6">
        {/* Header */}
        <div className="text-center">
          <h1 className="text-3xl font-bold text-slate-900">Describe Your Situation</h1>
          <p className="text-slate-500 mt-2">
            Tell us where you&apos;re going and who you&apos;re meeting — we&apos;ll pick the perfect outfit from your wardrobe and show you wearing it there.
          </p>
        </div>

        {/* Place Image Upload */}
        <Card className="shadow-lg">
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">📸 Upload a Photo of the Place <span className="text-slate-400 font-normal text-sm">(optional but recommended)</span></CardTitle>
            <CardDescription>A photo of the venue, environment or setting — restaurant, office, park, home, etc.</CardDescription>
          </CardHeader>
          <CardContent>
            {placePreview ? (
              <div className="relative">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={placePreview} alt="Place" className="w-full rounded-2xl object-cover max-h-64" />
                <button
                  onClick={() => { setPlaceFile(null); setPlacePrev(null); }}
                  className="absolute top-2 right-2 bg-white/90 rounded-full w-8 h-8 text-red-500 shadow hover:bg-white"
                >✕</button>
              </div>
            ) : (
              <div
                onDrop={handleDrop}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onClick={() => fileRef.current?.click()}
                className={`cursor-pointer rounded-2xl border-2 border-dashed p-10 text-center transition-all ${
                  dragOver ? "border-violet-500 bg-violet-50" : "border-violet-300 hover:border-violet-400 hover:bg-violet-50/50"
                }`}
              >
                <p className="text-4xl mb-2">🏙️</p>
                <p className="text-slate-600 font-medium">Drop a place photo here or click to browse</p>
                <p className="text-xs text-slate-400 mt-1">Restaurant, park, office, wedding hall, street…</p>
                <input ref={fileRef} type="file" accept="image/*" className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
              </div>
            )}
          </CardContent>
        </Card>

        {/* Situation Description */}
        <Card className="shadow-lg">
          <CardHeader>
            <CardTitle className="text-base">✍️ Describe the Situation</CardTitle>
            <CardDescription>Be as detailed as you like — the more context, the better the recommendation.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="text-sm font-medium text-slate-700 block mb-1.5">Where are you going? What is the setting?</label>
              <textarea
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500 resize-none"
                rows={4}
                placeholder="e.g. A rooftop restaurant in Mumbai with dim ambient lighting, semi-formal vibe, celebrating a friend's birthday with 10 people. The venue has warm wooden interiors and fairy lights."
                value={situationText}
                onChange={(e) => setSituationText(e.target.value)}
              />
              <p className="text-xs text-slate-400 mt-1">{situationText.length} chars · aim for 30+ for best results</p>
            </div>

            <div>
              <label className="text-sm font-medium text-slate-700 block mb-1.5">Who are you meeting? (optional)</label>
              <textarea
                className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-500 resize-none"
                rows={2}
                placeholder="e.g. Meeting a potential investor — need to look professional yet approachable. Or: First date with someone creative and artistic."
                value={personDesc}
                onChange={(e) => setPersonDesc(e.target.value)}
              />
            </div>
          </CardContent>
        </Card>

        {/* Example prompts */}
        <div>
          <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">Quick examples — click to use</p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => setSituationText(ex)}
                className="text-xs bg-white border border-violet-200 text-violet-700 rounded-full px-3 py-1.5 hover:bg-violet-50 transition-colors"
              >
                {ex.slice(0, 50)}…
              </button>
            ))}
          </div>
        </div>

        <Button
          onClick={handleSubmit}
          disabled={!canSubmit}
          size="lg"
          className="w-full"
        >
          {loading ? (
            <span className="flex items-center gap-2">
              <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              Analysing situation &amp; matching your wardrobe…
            </span>
          ) : (
            "✨ Find My Perfect Outfit"
          )}
        </Button>
      </div>
    </div>
  );
}

const EXAMPLES = [
  "Rooftop birthday dinner at a fancy restaurant, warm ambient lighting, friends and family, semi-formal vibe",
  "Job interview at a tech startup office, modern glass building, casual-professional culture",
  "First date at an art gallery opening, sophisticated crowd, evening event",
  "Beach wedding ceremony in Goa, outdoor, bright sunlight, tropical flowers, guests in colorful outfits",
  "College farewell party in the evening, open-air campus lawn, festive and fun atmosphere",
];

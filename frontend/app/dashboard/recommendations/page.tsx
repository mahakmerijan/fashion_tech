"use client";
import { useState, useEffect, useCallback, useRef } from "react";import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { API_BASE } from "@/lib/api";

interface OutfitItem {
  item_id?: string;
  category: string;
  description: string;
  color: string;
  from_wardrobe: boolean;
  image_url?: string;
}

interface Recommendation {
  outfit_id: string;
  title: string;
  rationale: string;
  items: OutfitItem[];
  missing_items: OutfitItem[];
  styling_tips: string[];
  color_suggestions: string[];
  place_outfit_compatibility?: string;
  confidence?: number;
}

interface ShoppingResult {
  name: string;
  price: string;
  url: string;
  image_url: string;
  platform: string;
  for_item?: string;
}

interface SituationResult {
  recommendation: Recommendation;
  recommendation_2?: Recommendation;
  composite_image_url: string;
  composite_image_url_2?: string;
  place_analysis: string;
  situation_text: string;
  person_description: string;
  shopping_results: ShoppingResult[];
  wardrobe_count: number;
}

export default function RecommendationsPage() {
  const router = useRouter();
  const [result, setResult] = useState<SituationResult | null>(null);
  const [placePreview, setPlacePreview] = useState<string | null>(null);
  const [generatingImage, setGeneratingImage] = useState(false);
  const [imageUrl, setImageUrl] = useState<string>("");
  const [imageUrl2, setImageUrl2] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");

  // Helper: returns true if a shopping item already exists in the wardrobe
  // Matches on BOTH category AND color — different color = different item, should still show
  const isInWardrobe = (forItem: string): boolean => {
    if (!forItem) return false;
    try {
      const raw = localStorage.getItem("fashion-tech-user");
      if (!raw) return false;
      const parsed = JSON.parse(raw);
      const wardrobe: Array<{ category?: string; primary_color?: string; sub_category?: string }> =
        parsed?.wardrobe || parsed?.state?.wardrobe || [];
      const needle = forItem.toLowerCase();
      return wardrobe.some((w) => {
        const cat = (w.sub_category || w.category || "").toLowerCase();
        const color = (w.primary_color || "").toLowerCase();
        if (!cat || !color) return false;
        // Must match BOTH category-type AND color to be considered "already owned"
        const catMatch = cat.split(" ").some(word => word.length > 3 && needle.includes(word));
        const colorMatch = color.length > 2 && needle.includes(color);
        return catMatch && colorMatch;
      });
    } catch { return false; }
  };
  const feedbackRef = useRef("");
  const [feedbackShoppingResults, setFeedbackShoppingResults] = useState<ShoppingResult[]>(() => {
    // Restore from sessionStorage on mount so results survive page reloads
    try {
      const saved = sessionStorage.getItem("feedback_shopping");
      return saved ? JSON.parse(saved) : [];
    } catch { return []; }
  });

  useEffect(() => {
    const raw = sessionStorage.getItem("situation_result");
    const b64 = sessionStorage.getItem("place_image_b64");
    const preview = b64 || sessionStorage.getItem("place_preview");
    if (raw) {
      const parsed: SituationResult = JSON.parse(raw);
      setResult(parsed);
      const API = API_BASE;
      const backendImg = parsed.composite_image_url || "";
      const isUsable = backendImg && !backendImg.includes("placehold") && !backendImg.startsWith("__");
      if (isUsable) {
        setImageUrl(backendImg.startsWith("http") ? backendImg : `${API}${backendImg}`);
      }
      // Second outfit image
      const backendImg2 = parsed.composite_image_url_2 || "";
      if (backendImg2 && !backendImg2.includes("placehold") && !backendImg2.startsWith("__")) {
        setImageUrl2(backendImg2.startsWith("http") ? backendImg2 : `${API}${backendImg2}`);
      }
    } else {
      setError("No recommendation data found. Please describe your situation first.");
    }
    if (preview) setPlacePreview(preview);
    setLoading(false);
  }, []);

  // Auto-generate images when recommendation is loaded
  useEffect(() => {
    if (!result || generatingImage) return;
    // Trigger if Look 1 is missing, OR if Look 2 data exists but Look 2 image is missing
    const hasLook2Data = result.recommendation_2 && Object.keys(result.recommendation_2).length > 0;
    if (!imageUrl || (hasLook2Data && !imageUrl2)) {
      generateImage();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  const generateImage = useCallback(async () => {
    if (!result) return;
    setGeneratingImage(true);
    try {
      const API = API_BASE;

      // ── Face profile from localStorage ───────────────────────────────────
      let faceProfile: Record<string, string> = {};
      let genderForDetect = "men";
      const storeRaw = localStorage.getItem("fashion-tech-user");
      try {
        if (storeRaw) {
          const parsed = JSON.parse(storeRaw);
          faceProfile = parsed?.faceProfile || parsed?.state?.faceProfile || {};
          const pref = parsed?.preferences || parsed?.state?.preferences || {};
          if (pref.gender) faceProfile = { ...faceProfile, gender: pref.gender };
          genderForDetect = (pref.gender || "men").toLowerCase() === "female" ? "women" : "men";
        }
      } catch { /* ignore */ }

      const selfieB64 = localStorage.getItem("selfie_b64") || undefined;
      const placeB64 = sessionStorage.getItem("place_image_b64") || undefined;
      const storedUserId = storeRaw ? (JSON.parse(storeRaw)?.userId || JSON.parse(storeRaw)?.state?.userId || "anonymous") : "anonymous";
      const wardrobeItems = storeRaw ? (JSON.parse(storeRaw)?.wardrobe || JSON.parse(storeRaw)?.state?.wardrobe || []) : [];

      const makeBody = (rec: Recommendation | undefined, feedback?: string) => ({
        outfit_id: rec?.outfit_id || crypto.randomUUID(),
        user_id: storedUserId,
        items: (rec?.items || []).map((i) => ({ item_id: i.item_id || crypto.randomUUID(), category: i.category, description: i.description, color: i.color })),
        face_profile: faceProfile,
        occasion: sessionStorage.getItem("situation_text") || "Casual",
        selfie_b64: selfieB64,
        place_b64: placeB64,
        user_feedback: feedback?.trim() || undefined,
      });

      const handleImageResult = (data: { image_url: string }, setter: (url: string) => void) => {
        if (data.image_url && !data.image_url.includes("placehold")) {
          const url = data.image_url.startsWith("http") ? data.image_url : `${API}${data.image_url}`;
          setter(url);
          const staticPath = data.image_url.startsWith("/static/") ? data.image_url : null;
          if (staticPath) {
            const budgetForShop = storeRaw ? (JSON.parse(storeRaw)?.preferences?.budget || JSON.parse(storeRaw)?.state?.preferences?.budget || "") : "";
            fetch(`${API}/api/products/detect-from-image`, {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ image_url: staticPath, wardrobe: wardrobeItems, gender: genderForDetect, budget: budgetForShop }),
            }).then(r => r.json()).then(d => {
              if (d.items?.length) setFeedbackShoppingResults(prev => {
                const urls = new Set(prev.map(x => x.url));
                const fresh = d.items.filter((x: ShoppingResult) => !urls.has(x.url));
                if (!fresh.length) return prev;
                const next = [...prev, ...fresh];
                try { sessionStorage.setItem("feedback_shopping", JSON.stringify(next)); } catch {}
                return next;
              });
            }).catch(() => {});
          }
        }
      };

      const currentFeedback = feedbackRef.current.trim();

      // Generate BOTH outfits in parallel
      const [res1, res2] = await Promise.all([
        fetch(`${API}/api/images/generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(makeBody(result.recommendation, currentFeedback)) }),
        result.recommendation_2 && Object.keys(result.recommendation_2).length > 0
          ? fetch(`${API}/api/images/generate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(makeBody(result.recommendation_2, currentFeedback)) })
          : Promise.resolve(null),
      ]);

      if (res1.ok) { const d = await res1.json(); handleImageResult(d, setImageUrl); }
      if (res2 && res2.ok) { const d = await res2.json(); handleImageResult(d, setImageUrl2); }

      // Extract items from feedback text
      if (currentFeedback) {
        fetch(`${API}/api/products/from-feedback`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ feedback: currentFeedback, gender: genderForDetect }),
        }).then(r => r.json()).then(d => {
          if (d.items?.length) setFeedbackShoppingResults(prev => {
            const urls = new Set(prev.map(x => x.url));
            const fresh = d.items.filter((x: ShoppingResult) => !urls.has(x.url));
            if (!fresh.length) return prev;
            const next = [...prev, ...fresh];
            try { sessionStorage.setItem("feedback_shopping", JSON.stringify(next)); } catch {}
            return next;
          });
        }).catch(() => {});
      }
    } catch (e) {
      console.error("Image generation failed:", e);
    } finally {
      setGeneratingImage(false);
    }
  }, [result]);

  if (loading) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4">
        <div className="w-16 h-16 border-4 border-violet-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-slate-600 text-lg font-medium">Loading your recommendation…</p>
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 p-6">
        <p className="text-5xl">😕</p>
        <p className="text-slate-700 font-medium">{error}</p>
        <Button onClick={() => router.push("/dashboard/occasion")}>Describe Your Situation</Button>
      </div>
    );
  }

  const rec = result.recommendation;

  return (
    <div className="min-h-screen bg-[#f8f7ff]">
      {/* Nav */}
      <nav className="bg-white border-b border-slate-200 px-6 py-4 flex items-center justify-between sticky top-0 z-10">
        <span className="font-bold text-xl text-violet-700">StyleAI ✨</span>
        <div className="flex gap-3 items-center">
          <Button variant="outline" size="sm" onClick={() => {
            sessionStorage.removeItem("feedback_shopping");
            router.push("/dashboard/occasion");
          }}>← New Situation</Button>
          <Button variant="ghost" size="sm" onClick={() => router.push("/dashboard")}>Dashboard</Button>
        </div>
      </nav>

      <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8 space-y-8">

        {/* ── Hero: Two Outfit Images ───────────────────────────────────────── */}
        <div className="flex flex-col items-center text-center gap-3">
          <h1 className="text-3xl font-extrabold text-violet-700 tracking-tight">✨ Your Outfit Options</h1>
          <p className="text-slate-500 text-sm max-w-xl">{result.situation_text}</p>

          {/* Two outfit images side by side */}
          <div className="w-full flex flex-col sm:flex-row gap-5 justify-center">
            {/* Outfit 1 */}
            <div className="flex-1 max-w-xs mx-auto sm:mx-0">
              <p className="text-xs font-bold text-violet-600 mb-2 uppercase tracking-widest">Option 1</p>
              <p className="text-xs text-slate-500 mb-2">{result.recommendation?.title}</p>
              {imageUrl && !imageUrl.includes("placehold.co") ? (
                <div className="rounded-3xl overflow-hidden shadow-2xl border-4 border-violet-200 aspect-[3/4]">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={imageUrl} alt="Outfit option 1" className="w-full h-full object-cover" />
                </div>
              ) : (
                <div className="rounded-3xl border-4 border-dashed border-violet-300 aspect-[3/4] flex flex-col items-center justify-center gap-4 bg-violet-50">
                  <div className="w-14 h-14 border-4 border-violet-500 border-t-transparent rounded-full animate-spin" />
                  <p className="text-sm font-semibold text-violet-600">{generatingImage ? "Generating look 1…" : "Preparing…"}</p>
                </div>
              )}
            </div>

            {/* Outfit 2 */}
            {result.recommendation_2 && Object.keys(result.recommendation_2).length > 0 && (
              <div className="flex-1 max-w-xs mx-auto sm:mx-0">
                <p className="text-xs font-bold text-indigo-600 mb-2 uppercase tracking-widest">Option 2</p>
                <p className="text-xs text-slate-500 mb-2">{result.recommendation_2?.title}</p>
                {imageUrl2 && !imageUrl2.includes("placehold.co") ? (
                  <div className="rounded-3xl overflow-hidden shadow-2xl border-4 border-indigo-200 aspect-[3/4]">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={imageUrl2} alt="Outfit option 2" className="w-full h-full object-cover" />
                  </div>
                ) : (
                  <div className="rounded-3xl border-4 border-dashed border-indigo-300 aspect-[3/4] flex flex-col items-center justify-center gap-4 bg-indigo-50">
                    <div className="w-14 h-14 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                    <p className="text-sm font-semibold text-indigo-600">Generating look 2…</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Feedback + Regenerate (Outfit 1) */}
          {imageUrl && !imageUrl.includes("placehold.co") && (
            <div className="w-full max-w-sm space-y-2">
              <div className="flex flex-col gap-1 text-left">
                <label className="text-xs font-medium text-slate-500">💬 Describe how you&apos;d like to change it</label>
                <textarea
                  rows={2}
                  placeholder="e.g. make it more formal, change jacket to white, add sunglasses…"
                  value={feedback}
                  onChange={(e) => { setFeedback(e.target.value); feedbackRef.current = e.target.value; }}
                  className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-400 resize-none"
                />
              </div>
              <Button onClick={generateImage} variant="outline" className="w-full" disabled={generatingImage}>
                {generatingImage ? "Regenerating both looks…" : "🔄 Regenerate Options"}
              </Button>
            </div>
          )}
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

          {/* Left: venue */}
          <div className="lg:col-span-2 space-y-4">
            {placePreview && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">📍 The Venue</CardTitle></CardHeader>
                <CardContent>
                  <div className="rounded-2xl overflow-hidden aspect-video">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={placePreview} alt="Place" className="w-full h-full object-cover"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                  </div>
                  {result.place_analysis && (
                    <p className="text-xs text-slate-500 mt-2 leading-relaxed">{result.place_analysis}</p>
                  )}
                </CardContent>
              </Card>
            )}
            {!placePreview && result.place_analysis && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">📍 Venue Analysis</CardTitle></CardHeader>
                <CardContent><p className="text-sm text-slate-600 leading-relaxed">{result.place_analysis}</p></CardContent>
              </Card>
            )}
          </div>

          {/* Right: outfit pieces + rationale */}
          <div className="lg:col-span-3 space-y-4">
            <Card>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="text-lg">{rec.title}</CardTitle>
                  {rec.place_outfit_compatibility && (
                    <Badge variant={rec.place_outfit_compatibility === "High" ? "default" : "secondary"} className="shrink-0">
                      {rec.place_outfit_compatibility} match
                    </Badge>
                  )}
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-slate-600 leading-relaxed">{rec.rationale}</p>
              </CardContent>
            </Card>

            {rec.items?.length > 0 && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">👔 Outfit Pieces from Your Wardrobe</CardTitle></CardHeader>
                <CardContent className="space-y-2">
                  {rec.items.map((item: OutfitItem, i: number) => (
                    <div key={i} className="flex items-center gap-3 p-3 bg-violet-50 border border-violet-100 rounded-xl">
                      {item.image_url ? (
                        /* eslint-disable-next-line @next/next/no-img-element */
                        <img src={item.image_url} alt={item.description} className="w-14 h-14 rounded-lg object-cover border border-violet-200 flex-shrink-0" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                      ) : (
                        <span className="text-2xl flex-shrink-0">{categoryEmoji(item.category)}</span>
                      )}
                      <div className="min-w-0">
                        <p className="font-medium text-sm text-slate-800 truncate">{item.description}</p>
                        <p className="text-xs text-slate-400">{item.color} · {item.category}</p>
                      </div>
                      {item.from_wardrobe && <Badge variant="secondary" className="ml-auto text-xs shrink-0">Your wardrobe</Badge>}
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}

            {rec.styling_tips?.length > 0 && (
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-base">💡 Styling Tips</CardTitle></CardHeader>
                <CardContent>
                  <ul className="space-y-1.5">
                    {rec.styling_tips.map((tip: string, i: number) => (
                      <li key={i} className="flex gap-2 text-sm text-slate-600">
                        <span className="text-violet-500 mt-0.5 flex-shrink-0">•</span>{tip}
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            )}
          </div>
        </div>

        {/* ── Shopping Section ───────────────────────────────────────────────── */}
        {(rec.missing_items?.length > 0 || result.shopping_results?.length > 0 || feedbackShoppingResults.length > 0) && (
          <div>
            <h2 className="text-xl font-bold text-slate-800 mb-1">🛍️ Complete the Look</h2>
            <p className="text-sm text-slate-500 mb-5">These items aren&apos;t in your wardrobe — shop them now</p>

            {/* Missing item chips */}
            {rec.missing_items?.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-6">
                {rec.missing_items.map((item: OutfitItem, i: number) => (
                  <div key={i} className="flex items-center gap-2 px-3 py-1.5 bg-orange-50 border border-orange-200 rounded-full text-sm text-slate-700">
                    <span>{categoryEmoji(item.category)}</span>
                    <span className="font-medium">{item.color} {item.description}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Original shopping results — filtered to exclude wardrobe items */}
            {result.shopping_results?.length > 0 && (
              <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
                {result.shopping_results
                  .filter((r: ShoppingResult) => !isInWardrobe(r.for_item || ""))
                  .map((r: ShoppingResult, i: number) => (
                    <ProductCard key={i} result={r} />
                  ))}
              </div>
            )}

            {/* Feedback-based additions — filtered to exclude wardrobe items */}
            {feedbackShoppingResults.filter(r => !isInWardrobe(r.for_item || "")).length > 0 && (
              <div className="mt-6">
                <div className="flex items-center gap-2 mb-4">
                  <span className="text-sm font-semibold text-violet-700">✨ Spotted in your look — not in wardrobe</span>
                  <span className="text-xs bg-violet-100 text-violet-600 rounded-full px-2 py-0.5">Shop Now</span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-4">
                  {feedbackShoppingResults
                    .filter((r: ShoppingResult) => !isInWardrobe(r.for_item || ""))
                    .map((r: ShoppingResult, i: number) => (
                      <ProductCard key={`fb-${i}`} result={r} />
                    ))}
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function categoryEmoji(cat: string) {
  const map: Record<string, string> = {
    Shirt: "👕", Pants: "👖", Shoes: "👟", Jacket: "🧥", Tie: "👔",
    Watch: "⌚", Bag: "👜", Accessories: "💍", Dress: "👗", Shorts: "🩳",
    Kurta: "👘", Belt: "🪢", Outerwear: "🧣",
  };
  return map[cat] ?? "👔";
}

// ── ProductCard: shows Shopify image when available, else Gemini-generated ────
function ProductCard({ result: r }: { result: ShoppingResult }) {
  const API = API_BASE;
  const [imgSrc, setImgSrc] = useState<string | null>(
    // Use Shopify image_url directly if provided (from Snitch/Rare Rabbit Shopify API)
    r.image_url && r.image_url.startsWith("http") ? r.image_url : null
  );

  const forItem = r.for_item || "";
  const category = (r.name?.split(" on ")[0] || "").toLowerCase().replace(/\s+/g, "-");
  const colorMatch = forItem.match(/\b(navy|black|white|grey|gray|brown|beige|red|blue|green|cream|khaki|olive|orange|pink|purple|tan)\b/i);
  const color = colorMatch ? colorMatch[1].toLowerCase() : "";
  const imgUrl = `${API}/api/products/image?category=${encodeURIComponent(category)}&color=${encodeURIComponent(color)}&description=${encodeURIComponent(forItem.slice(0, 60))}`;

  const platformColor: Record<string, string> = {
    "Snitch": "bg-orange-100 text-orange-700",
    "Rare Rabbit": "bg-purple-100 text-purple-700",
    "Amazon": "bg-yellow-100 text-yellow-800",
    "Flipkart": "bg-blue-100 text-blue-700",
  };
  const badgeClass = platformColor[r.platform] || "bg-slate-100 text-slate-600";

  return (
    <a
      href={r.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex flex-col rounded-2xl border border-slate-200 bg-white overflow-hidden hover:border-violet-400 hover:shadow-lg transition-all"
    >
      {/* Product image */}
      <div className="aspect-square bg-slate-50 relative overflow-hidden">
        {imgSrc ? (
          <img src={imgSrc} alt={forItem || category} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" onError={() => setImgSrc("")} />
        ) : (
          <img
            src={imgUrl}
            alt={forItem || category}
            className="w-full h-full object-cover"
            onLoad={(e) => setImgSrc((e.target as HTMLImageElement).src)}
            onError={() => setImgSrc("")}
          />
        )}
      </div>
      {/* Info */}
      <div className="p-3 flex flex-col gap-1">
        {r.for_item && <p className="text-xs text-slate-700 line-clamp-2 font-medium">{r.for_item}</p>}
        {r.name && r.name !== r.for_item && <p className="text-xs text-slate-500 line-clamp-1 italic">{r.name}</p>}
        <div className="flex items-center justify-between gap-1 mt-auto pt-1">
          <span className="text-xs font-bold text-green-700">{r.price || ""}</span>
          <span className={`text-xs font-semibold rounded-full px-2 py-0.5 ${badgeClass}`}>
            {r.platform} →
          </span>
        </div>
      </div>
    </a>
  );
}




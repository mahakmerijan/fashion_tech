"use client";
import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { useUserStore } from "@/stores/user-store";
import { uploadWardrobeItems, deleteWardrobeItem } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function resolveImageUrl(url: string): string {
  if (!url) return "";
  if (url.startsWith("http")) return url;
  return `${API}${url}`;
}

interface LocalItem { localId: string; previewUrl: string; file: File; status: "pending" | "uploading" | "done" | "error"; serverItem?: Record<string, unknown>; }

export default function WardrobePage() {
  const router = useRouter();
  const { userId, wardrobe, addWardrobeItems, removeWardrobeItem, setOnboardingStep } = useUserStore();
  const [localItems, setLocalItems] = useState<LocalItem[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((files: File[]) => {
    const valid = files.filter(f => f.type.startsWith("image/")).slice(0, 50);
    const newItems: LocalItem[] = valid.map(f => ({
      localId: `${Date.now()}_${Math.random()}`,
      previewUrl: URL.createObjectURL(f),
      file: f,
      status: "pending",
    }));
    setLocalItems(prev => [...prev, ...newItems]);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    addFiles(files);
  }, [addFiles]);

  const BATCH_SIZE = 5; // 5 images per request → backend processes 3 concurrently → ~10-15s per batch

  const uploadAll = useCallback(async () => {
    const pending = localItems.filter(i => i.status === "pending");
    if (!pending.length) return;

    // Mark all pending as "uploading"
    setLocalItems(prev =>
      prev.map(i => pending.some(p => p.localId === i.localId) ? { ...i, status: "uploading" } : i)
    );

    // Chunk into batches of BATCH_SIZE and process sequentially
    for (let batchStart = 0; batchStart < pending.length; batchStart += BATCH_SIZE) {
      const batch = pending.slice(batchStart, batchStart + BATCH_SIZE);

      try {
        // ONE request per chunk — all images in the same FormData
        const fd = new FormData();
        batch.forEach(item => fd.append("images", item.file));

        const result = await uploadWardrobeItems(userId || "temp", fd) as { items: Record<string, unknown>[] };
        const serverItems = result.items || [];
        addWardrobeItems(serverItems as unknown as Parameters<typeof addWardrobeItems>[0]);

        // Mark each item in this batch as done
        setLocalItems(prev =>
          prev.map(i => {
            const idx = batch.findIndex(b => b.localId === i.localId);
            if (idx === -1) return i;
            return { ...i, status: "done", serverItem: serverItems[idx] };
          })
        );
      } catch (batchErr) {
        console.error(`Batch ${batchStart / BATCH_SIZE + 1} failed, retrying individually:`, batchErr);
        // Retry images in this chunk individually so partial success is captured
        await Promise.allSettled(
          batch.map(async (item) => {
            try {
              const fd = new FormData();
              fd.append("images", item.file);
              const result = await uploadWardrobeItems(userId || "temp", fd) as { items: Record<string, unknown>[] };
              const serverItems = result.items || [];
              addWardrobeItems(serverItems as unknown as Parameters<typeof addWardrobeItems>[0]);
              setLocalItems(prev =>
                prev.map(i => i.localId === item.localId ? { ...i, status: "done", serverItem: serverItems[0] } : i)
              );
            } catch {
              setLocalItems(prev => prev.map(i => i.localId === item.localId ? { ...i, status: "error" } : i));
            }
          })
        );
      }
    }
  }, [localItems, userId, addWardrobeItems]);

  const removeLocal = (localId: string) => {
    setLocalItems(prev => {
      const item = prev.find(i => i.localId === localId);
      if (item) URL.revokeObjectURL(item.previewUrl);
      return prev.filter(i => i.localId !== localId);
    });
  };

  const removeServer = async (itemId: string) => {
    removeWardrobeItem(itemId);
    if (userId) await deleteWardrobeItem(userId, itemId).catch(() => {});
  };

  const handleFinish = () => {
    setOnboardingStep(4);
    router.push("/dashboard");
  };

  const uploadedThisSession = localItems.filter(i => i.status === "done").length;
  const totalDone = wardrobe.length; // wardrobe store already contains all uploaded items

  return (
    <div className="min-h-screen flex flex-col items-center p-6 bg-gradient-to-br from-violet-50 to-indigo-100">
      <div className="w-full max-w-2xl mb-8 mt-8">
        <div className="flex justify-between text-xs text-slate-500 mb-2">
          <span>Step 4 of 5</span>
          <span>80% complete</span>
        </div>
        <Progress value={80} />
      </div>

      <Card className="w-full max-w-2xl shadow-xl">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">Build Your Wardrobe</CardTitle>
          <CardDescription>
            Upload photos of your clothes — shirts, pants, shoes, jackets, accessories. <br />
            AI will automatically tag and categorise each item.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Drop zone */}
          <div
            onDrop={handleDrop}
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onClick={() => fileInputRef.current?.click()}
            className={`cursor-pointer rounded-2xl border-2 border-dashed p-10 text-center transition-all ${
              dragOver ? "border-violet-500 bg-violet-50" : "border-violet-300 hover:border-violet-400 hover:bg-violet-50/50"
            }`}
          >
            <p className="text-4xl mb-3">👗</p>
            <p className="font-semibold text-slate-700">Drag & drop clothing photos here</p>
            <p className="text-sm text-slate-400 mt-1">or click to browse · JPG, PNG · up to 10MB each</p>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              onChange={(e) => addFiles(Array.from(e.target.files || []))}
              className="hidden"
            />
          </div>

          {/* Stats */}
          {(localItems.length > 0 || wardrobe.length > 0) && (
            <div className="flex items-center gap-4 text-sm text-slate-600">
              <Badge variant="secondary">{localItems.length} selected</Badge>
              <Badge variant="default">{totalDone} uploaded</Badge>
              {localItems.some(i => i.status === "pending") && (
                <Button size="sm" onClick={uploadAll}>⬆ Upload All</Button>
              )}
            </div>
          )}

          {/* Grid preview */}
          {localItems.length > 0 && (
            <div className="grid grid-cols-3 sm:grid-cols-4 gap-3">
              {localItems.map((item) => (
                <div key={item.localId} className="relative group rounded-xl overflow-hidden aspect-square border border-slate-200 bg-slate-50">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.previewUrl} alt="" className="w-full h-full object-cover" />
                  {/* Status overlay */}
                  {item.status === "uploading" && (
                    <div className="absolute inset-0 bg-black/40 flex items-center justify-center">
                      <div className="w-6 h-6 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    </div>
                  )}
                  {item.status === "done" && (
                    <div className="absolute inset-0 bg-green-500/30 flex items-end p-1">
                      <span className="bg-green-500 text-white text-xs rounded px-1.5 py-0.5">✓ Done</span>
                    </div>
                  )}
                  {item.status === "error" && (
                    <div className="absolute inset-0 bg-red-500/30 flex items-end p-1">
                      <span className="bg-red-500 text-white text-xs rounded px-1.5 py-0.5">✗ Error</span>
                    </div>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); removeLocal(item.localId); }}
                    className="absolute top-1 right-1 w-6 h-6 bg-white/90 rounded-full text-xs text-red-500 opacity-0 group-hover:opacity-100 transition-opacity shadow"
                  >✕</button>
                </div>
              ))}
            </div>
          )}

          {/* Server wardrobe items — just show count, no grid */}
          {wardrobe.length > 0 && (
            <div className="flex items-center gap-2 px-4 py-3 bg-violet-50 border border-violet-200 rounded-xl">
              <span className="text-violet-600 text-lg">✓</span>
              <p className="text-sm font-medium text-slate-700">{wardrobe.length} item{wardrobe.length !== 1 ? "s" : ""} uploaded to your wardrobe</p>
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <Button variant="outline" onClick={() => router.back()} className="w-28">← Back</Button>
            <Button
              onClick={handleFinish}
              className="flex-1"
              size="lg"
            >
              {totalDone > 0 ? `Continue with ${totalDone} items →` : "Skip for now →"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

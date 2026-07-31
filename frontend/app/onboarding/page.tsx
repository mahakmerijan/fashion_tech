"use client";
import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useUserStore } from "@/stores/user-store";
import { analyzeFace, createProfile } from "@/lib/api";

type Mode = "idle" | "camera" | "uploading" | "analyzing" | "done" | "error";

export default function OnboardingPage() {
  const router = useRouter();
  const { setSelfie, setFaceProfile, setUserId, setOnboardingStep, clearWardrobe, userId } = useUserStore();
  const [mode, setMode] = useState<Mode>("idle");
  const [preview, setPreview] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Start webcam
  const startCamera = useCallback(async () => {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: 640, height: 480 } });
      setStream(s);
      setMode("camera");
      setTimeout(() => { if (videoRef.current) videoRef.current.srcObject = s; }, 100);
    } catch {
      setErrorMsg("Camera access denied. Please allow camera permissions.");
      setMode("error");
    }
  }, []);

  // Capture selfie from webcam
  const captureSelfie = useCallback(() => {
    if (!videoRef.current || !canvasRef.current) return;
    const ctx = canvasRef.current.getContext("2d")!;
    canvasRef.current.width = videoRef.current.videoWidth;
    canvasRef.current.height = videoRef.current.videoHeight;
    ctx.drawImage(videoRef.current, 0, 0);
    canvasRef.current.toBlob((blob) => {
      if (!blob) return;
      const f = new File([blob], "selfie.jpg", { type: "image/jpeg" });
      const url = URL.createObjectURL(blob);
      setFile(f);
      setPreview(url);
      stream?.getTracks().forEach((t) => t.stop());
      setStream(null);
      setMode("uploading");
    }, "image/jpeg", 0.85);
  }, [stream]);

  // Handle file upload
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    if (!f.type.startsWith("image/")) { setErrorMsg("Please upload an image file."); setMode("error"); return; }
    if (f.size > 10 * 1024 * 1024) { setErrorMsg("Image must be under 10MB."); setMode("error"); return; }
    setFile(f);
    setPreview(URL.createObjectURL(f));
    setMode("uploading");
  };

  // Submit image for analysis
  const analyzeImage = async () => {
    if (!file) return;
    setMode("analyzing");
    try {
      const fd = new FormData();
      fd.append("image", file);
      // Compress to 1024px max on client side
      const compressedFile = await compressImage(file, 1024);
      fd.set("image", compressedFile);
      // Pass authenticated user_id so backend updates their existing profile
      if (userId) fd.append("user_id", userId);

      const data = await analyzeFace(fd) as { user_id: string; face_profile: Record<string, string> };
      setUserId(data.user_id);
      clearWardrobe();   // fresh session — discard wardrobe from any previous run
      setSelfie(preview!, file);
      setFaceProfile(data.face_profile as unknown as Parameters<typeof setFaceProfile>[0]);

      // Save compressed selfie as base64 to localStorage so the recommendations
      // page can pass it to Puter.js as an input image for compositing.
      try {
        const b64 = await fileToBase64(compressedFile);
        localStorage.setItem("selfie_b64", b64);
      } catch { /* non-critical */ }

      setOnboardingStep(1);
      setMode("done");
      setTimeout(() => router.push("/onboarding/gender"), 500);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "Analysis failed. Please try again.");
      setMode("error");
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-gradient-to-br from-violet-50 to-indigo-100">
      {/* Progress */}
      <div className="w-full max-w-md mb-8">
        <div className="flex justify-between text-xs text-slate-500 mb-2">
          <span>Step 1 of 5</span>
          <span>20% complete</span>
        </div>
        <Progress value={20} />
      </div>

      <Card className="w-full max-w-md shadow-xl">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">Upload Your Photo</CardTitle>
          <CardDescription>We&apos;ll analyse your face shape, skin tone, and style personality</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {mode === "idle" && (
            <div className="grid grid-cols-2 gap-4">
              <button
                onClick={startCamera}
                className="flex flex-col items-center gap-3 p-6 rounded-2xl border-2 border-dashed border-violet-300 hover:border-violet-500 hover:bg-violet-50 transition-all cursor-pointer"
              >
                <span className="text-4xl">📸</span>
                <span className="font-medium text-slate-700">Take Selfie</span>
                <span className="text-xs text-slate-400 text-center">Use your camera</span>
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex flex-col items-center gap-3 p-6 rounded-2xl border-2 border-dashed border-violet-300 hover:border-violet-500 hover:bg-violet-50 transition-all cursor-pointer"
              >
                <span className="text-4xl">🖼️</span>
                <span className="font-medium text-slate-700">Upload Photo</span>
                <span className="text-xs text-slate-400 text-center">From your device</span>
              </button>
              <input ref={fileInputRef} type="file" accept="image/*" onChange={handleFileChange} className="hidden" />
            </div>
          )}

          {mode === "camera" && (
            <div className="space-y-4">
              <div className="relative rounded-2xl overflow-hidden bg-black aspect-[4/3]">
                <video ref={videoRef} autoPlay playsInline muted className="w-full h-full object-cover" />
                <div className="absolute inset-0 border-4 border-violet-400 rounded-2xl pointer-events-none" />
              </div>
              <canvas ref={canvasRef} className="hidden" />
              <div className="flex gap-3">
                <Button onClick={captureSelfie} className="flex-1">📸 Capture</Button>
                <Button variant="outline" onClick={() => { stream?.getTracks().forEach(t => t.stop()); setMode("idle"); }}>Cancel</Button>
              </div>
            </div>
          )}

          {(mode === "uploading" || mode === "analyzing" || mode === "done") && preview && (
            <div className="space-y-4">
              <div className="relative rounded-2xl overflow-hidden aspect-square max-w-xs mx-auto">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={preview} alt="Your photo" className="w-full h-full object-cover" />
                {mode === "analyzing" && (
                  <div className="absolute inset-0 bg-black/50 flex flex-col items-center justify-center text-white">
                    <div className="w-12 h-12 border-4 border-white border-t-transparent rounded-full animate-spin mb-3" />
                    <p className="text-sm font-medium">Analysing your features…</p>
                  </div>
                )}
                {mode === "done" && (
                  <div className="absolute inset-0 bg-green-500/80 flex items-center justify-center text-white text-4xl">
                    ✓
                  </div>
                )}
              </div>
              {mode === "uploading" && (
                <div className="flex gap-3">
                  <Button onClick={analyzeImage} className="flex-1">Analyse My Photo ✨</Button>
                  <Button variant="outline" onClick={() => { setPreview(null); setFile(null); setMode("idle"); }}>Retake</Button>
                </div>
              )}
            </div>
          )}

          {mode === "error" && (
            <div className="text-center space-y-4">
              <p className="text-red-500 text-sm">{errorMsg}</p>
              <Button onClick={() => setMode("idle")} variant="outline">Try Again</Button>
            </div>
          )}
        </CardContent>
      </Card>

      <p className="mt-6 text-xs text-slate-400 text-center max-w-xs">
        Your photo is processed securely and never shared. We only store extracted face features, not the raw image.
      </p>
    </div>
  );
}

async function compressImage(file: File, maxSize: number): Promise<File> {  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, maxSize / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = img.width * scale;
      canvas.height = img.height * scale;
      canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        URL.revokeObjectURL(url);
        resolve(blob ? new File([blob], file.name, { type: "image/jpeg" }) : file);
      }, "image/jpeg", 0.85);
    };
    img.src = url;
  });
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

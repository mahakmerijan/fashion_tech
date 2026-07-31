"use client";
import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function LandingPage() {
  return (
    <main className="min-h-screen flex flex-col">
      {/* Hero */}
      <section className="gradient-hero flex-1 flex flex-col items-center justify-center text-white px-6 py-24 text-center">
        <div className="page-enter max-w-3xl">
          <h1 className="text-7xl md:text-9xl font-black mb-4 leading-none tracking-tight">
            <span className="bg-gradient-to-r from-white via-yellow-200 to-yellow-400 bg-clip-text text-transparent drop-shadow-2xl">
              StyleAI
            </span>
          </h1>
          <p className="text-lg md:text-xl text-white/70 font-medium mb-8 tracking-widest uppercase">
            Your Personal Fashion Intelligence
          </p>
          <p className="text-xl md:text-2xl text-white/80 mb-10 max-w-2xl mx-auto">
            Upload a selfie, build your wardrobe, and get AI-powered outfit recommendations
            tailored to your face shape, skin tone, and style personality.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link href="/auth/signup">
              <Button size="lg" className="bg-white text-violet-700 hover:bg-white/90 text-base font-semibold px-10">
                Get Started Free
              </Button>
            </Link>
            <Link href="/dashboard">
              <Button size="lg" variant="outline" className="border-white text-white hover:bg-white/10 text-base font-semibold px-10">
                View Demo
              </Button>
            </Link>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="bg-white py-20 px-6">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-3xl font-bold text-center mb-4 text-slate-900">How It Works</h2>
          <p className="text-center text-slate-500 mb-14 text-lg">Four simple steps to your perfect outfit</p>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-8">
            {steps.map((s, i) => (
              <div key={i} className="flex flex-col items-center text-center">
                <div className="w-16 h-16 rounded-2xl gradient-hero flex items-center justify-center text-white text-2xl font-bold mb-4 shadow-lg">
                  {i + 1}
                </div>
                <h3 className="font-semibold text-slate-900 mb-2">{s.title}</h3>
                <p className="text-sm text-slate-500">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Tech */}
      <section className="bg-slate-50 py-16 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-2xl font-bold mb-8 text-slate-900">Powered By</h2>
          <div className="flex flex-wrap justify-center gap-4">
            {["Amazon", "Flipkart", "Myntra", "AJIO"].map((t) => (
              <span key={t} className="bg-white border border-violet-200 text-violet-700 rounded-full px-4 py-1.5 text-sm font-medium shadow-sm">
                {t}
              </span>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}

const steps = [
  { title: "Upload Your Selfie", desc: "Take a selfie or upload a photo. AI analyses your face shape, skin tone, and style personality." },
  { title: "Answer Style Quiz", desc: "Tell us your fashion preferences – fit, colours, fabrics, budget, and how bold you want to go." },
  { title: "Build Your Wardrobe", desc: "Upload photos of your existing clothes. AI tags, categorises, and indexes everything automatically." },
  { title: "Get Recommendations", desc: "Pick an occasion. Receive curated outfits from your wardrobe with AI-generated virtual try-on images." },
];

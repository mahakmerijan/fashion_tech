"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { useUserStore } from "@/stores/user-store";
import { updatePreferences } from "@/lib/api";

type Step = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

// ── Style Picker (Step 0) ───────────────────────────────────────────────────
const STYLES = [
  { value: "Street Style", key: "street" },
  { value: "Classic",      key: "classic" },
  { value: "Casual",       key: "casual" },
  { value: "Edgy",         key: "edgy" },
];

// ── Price Range Picker (Step 1) ────────────────────────────────────────────
const PRICE_RANGES = [
  { value: "Under ₹500",      label: "Under ₹500",       sub: "Budget-friendly" },
  { value: "₹500–₹1,500",    label: "₹500 – ₹1,500",    sub: "Affordable everyday" },
  { value: "₹1,500–₹3,000",  label: "₹1,500 – ₹3,000",  sub: "Mid-range quality" },
  { value: "₹3,000–₹7,000",  label: "₹3,000 – ₹7,000",  sub: "Premium picks" },
  { value: "₹7,000–₹15,000", label: "₹7,000 – ₹15,000", sub: "Luxury essentials" },
  { value: "Above ₹15,000",   label: "Above ₹15,000",    sub: "High-end fashion" },
];

// ── Preference Questions (Steps 1–6) ─────────────────────────────────────────
const QUESTIONS = [
  {
    key: "fit",
    title: "What's your preferred fit?",
    subtitle: "Choose what feels most comfortable and stylish to you",
    type: "single",
    options: [
      { value: "Slim Fit",    emoji: "🩱", desc: "Close to body, clean lines" },
      { value: "Regular Fit", emoji: "👕", desc: "Classic relaxed comfort" },
      { value: "Loose Fit",   emoji: "🧥", desc: "Relaxed & breathable" },
      { value: "Oversized",   emoji: "🪆", desc: "Bold & streetwear-inspired" },
    ],
  },
  {
    key: "favorite_colors",
    title: "What colors do you love wearing?",
    subtitle: "Select all that apply",
    type: "multi",
    options: [
      { value: "Black",        emoji: "⬛", desc: "" },
      { value: "White",        emoji: "⬜", desc: "" },
      { value: "Navy",         emoji: "🔵", desc: "" },
      { value: "Grey",         emoji: "🩶", desc: "" },
      { value: "Earthy Tones", emoji: "🟤", desc: "Beige, camel, brown" },
      { value: "Pastels",      emoji: "🎀", desc: "Soft pinks, blues, lavender" },
      { value: "Bright Colors",emoji: "🌈", desc: "Reds, yellows, greens" },
      { value: "Jewel Tones",  emoji: "💎", desc: "Emerald, sapphire, ruby" },
    ],
  },
  {
    key: "footwear",
    title: "Favourite footwear style?",
    subtitle: "Pick one primary style",
    type: "single",
    options: [
      { value: "Sneakers",          emoji: "👟", desc: "" },
      { value: "Formal Shoes",      emoji: "👞", desc: "" },
      { value: "Loafers",           emoji: "🥿", desc: "" },
      { value: "Boots",             emoji: "👢", desc: "" },
      { value: "Sandals / Slippers",emoji: "🩴", desc: "" },
      { value: "Sports Shoes",      emoji: "⚽", desc: "" },
    ],
  },
  {
    key: "fabrics",
    title: "Which fabrics do you prefer?",
    subtitle: "Select all that apply",
    type: "multi",
    options: [
      { value: "Cotton",               emoji: "☁️", desc: "Breathable & casual" },
      { value: "Linen",                emoji: "🌾", desc: "Light & summery" },
      { value: "Denim",                emoji: "👖", desc: "Durable & classic" },
      { value: "Wool",                 emoji: "🐑", desc: "Warm & formal" },
      { value: "Silk / Satin",         emoji: "✨", desc: "Smooth & luxurious" },
      { value: "Synthetic (Polyester)",emoji: "🧴", desc: "Easy care & sporty" },
      { value: "Knit / Jersey",        emoji: "🧶", desc: "Stretchy & comfortable" },
    ],
  },
  {
    key: "priority",
    title: "What do you value most in clothing?",
    subtitle: "Pick your top priority",
    type: "single",
    options: [
      { value: "Style",       emoji: "✨", desc: "Looking great is non-negotiable" },
      { value: "Comfort",     emoji: "🛋️", desc: "Comfort first, always" },
      { value: "Durability",  emoji: "🔩", desc: "Built to last" },
      { value: "Versatility", emoji: "🔄", desc: "Works for multiple occasions" },
    ],
  },
  {
    key: "experiment_level",
    title: "How bold is your style experimentation?",
    subtitle: "1 = very safe, 5 = fashion-forward & experimental",
    type: "scale",
    options: [
      { value: "1", emoji: "😌", desc: "Stick to classics" },
      { value: "2", emoji: "🙂", desc: "Mostly safe" },
      { value: "3", emoji: "😊", desc: "Open to new ideas" },
      { value: "4", emoji: "😎", desc: "Love experimenting" },
      { value: "5", emoji: "🤩", desc: "Push every boundary" },
    ],
  },
  // NOTE: budget is handled by the dedicated Price Range step (step 1)
  // keeping the array clean — no budget question here
];

export default function QuestionnairePage() {
  const router = useRouter();
  const { userId, preferences, setPreferences, setOnboardingStep } = useUserStore();
  const gender = (preferences.gender || "").toLowerCase();
  const imgSuffix = gender === "female" ? "female" : "male";

  // step 0 = style picker, steps 1–6 = QUESTIONS[0–5]
  const [step, setStep] = useState<Step>(0);
  const [selectedStyles, setSelectedStyles] = useState<string[]>([]);
  const [selectedBudget, setSelectedBudget] = useState<string>("");
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [saving, setSaving] = useState(false);

  // ── Style Picker handlers ────────────────────────────────────────────────
  const toggleStyle = (value: string) => {
    setSelectedStyles((prev) =>
      prev.includes(value)
        ? prev.filter((s) => s !== value)
        : prev.length < 2 ? [...prev, value] : prev
    );
  };

  // ── Question handlers ────────────────────────────────────────────────────
  const q = step > 0 ? QUESTIONS[step - 1] : null;
  const current = q ? answers[q.key] : undefined;

  const toggle = (value: string) => {
    if (!q) return;
    if (q.type === "single" || q.type === "scale") {
      setAnswers((a) => ({ ...a, [q.key]: value }));
    } else {
      const arr = (current as string[] | undefined) ?? [];
      setAnswers((a) => ({
        ...a,
        [q.key]: arr.includes(value) ? arr.filter((x) => x !== value) : [...arr, value],
      }));
    }
  };

  const canAdvanceQ = q
    ? q.type === "multi"
      ? ((current as string[] | undefined)?.length ?? 0) > 0
      : !!current
    : false;

  const handleNext = async () => {
    if (step === 0) {
      // style picker → price range
      setAnswers((a) => ({ ...a, style_personality: selectedStyles.join(", ") }));
      setStep(1);
      return;
    }
    if (step === 1) {
      // price range → first question
      setAnswers((a) => ({ ...a, budget: selectedBudget }));
      setStep(2);
      return;
    }
    if (step < QUESTIONS.length + 1) {
      setStep((s) => (s + 1) as Step);
    } else {
      setSaving(true);
      const prefs = {
        ...answers,
        budget: answers.budget || selectedBudget,
        experiment_level: parseInt(answers.experiment_level as string || "3"),
      };
      setPreferences(prefs as Parameters<typeof setPreferences>[0]);
      if (userId) await updatePreferences(userId, prefs).catch(() => {});
      setOnboardingStep(3);
      router.push("/onboarding/wardrobe");
    }
  };

  const totalSteps = QUESTIONS.length + 2; // style picker + price range + questions
  const progress = 40 + Math.round((step / totalSteps) * 20);

  // ── Style Picker UI ────────────────────────────────────────────────────────
  if (step === 0) {
    return (
      <div className="min-h-screen flex flex-col bg-[#0d0d1a]">
        {/* Progress */}
        <div className="px-6 pt-8 pb-4">
          <div className="flex justify-between text-xs text-slate-400 mb-2">
            <span>Step 3 of 5 · Style Picker</span>
            <span>40% complete</span>
          </div>
          <Progress value={40} />
        </div>

        {/* Header */}
        <div className="px-6 pb-6 text-center">
          <h1 className="text-2xl font-bold text-white leading-tight">
            Choose your desired styles.<br />I&apos;ll do the rest.
          </h1>
          <p className="text-slate-400 text-sm mt-2">You can pick up to 2 styles</p>
        </div>

        {/* 2×2 Style Grid */}
        <div className="flex-1 px-4 grid grid-cols-2 gap-3 max-w-lg mx-auto w-full">
          {STYLES.map((style) => {
            const selected = selectedStyles.includes(style.value);
            return (
              <button
                key={style.key}
                onClick={() => toggleStyle(style.value)}
                className={`relative rounded-2xl overflow-hidden aspect-[3/4] border-2 transition-all ${
                  selected ? "border-violet-500 scale-[0.97]" : "border-transparent"
                }`}
              >
                <Image
                  src={`/styles/${style.key}_${imgSuffix}.jpg`}
                  alt={style.value}
                  fill
                  className="object-cover"
                  sizes="(max-width: 768px) 50vw, 200px"
                />
                {/* Gradient overlay */}
                <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent" />
                {/* Checkbox */}
                <div className={`absolute top-3 left-3 w-6 h-6 rounded-md border-2 flex items-center justify-center transition-all ${
                  selected ? "bg-violet-500 border-violet-500" : "bg-white/20 border-white/60"
                }`}>
                  {selected && <span className="text-white text-xs font-bold">✓</span>}
                </div>
                {/* Style label */}
                <div className="absolute bottom-3 left-3 right-3">
                  <p className="text-white font-black text-xl uppercase tracking-widest leading-tight drop-shadow-lg">
                    {style.value.split(" ").map((word, i) => (
                      <span key={i} className="block">{word}</span>
                    ))}
                  </p>
                </div>
              </button>
            );
          })}
        </div>

        {/* Continue */}
        <div className="px-4 py-6 max-w-lg mx-auto w-full">
          <Button
            onClick={handleNext}
            disabled={selectedStyles.length === 0}
            className="w-full h-14 text-base font-semibold tracking-widest uppercase rounded-2xl bg-violet-600 hover:bg-violet-700 disabled:bg-slate-700 disabled:text-slate-500"
          >
            CONTINUE
          </Button>
        </div>
      </div>
    );
  }

  // ── Price Range Picker UI (Step 1) ────────────────────────────────────────
  if (step === 1) {
    return (
      <div className="min-h-screen flex flex-col bg-[#0d0d1a]">
        {/* Progress */}
        <div className="px-6 pt-8 pb-4">
          <div className="flex justify-between text-xs text-slate-400 mb-2">
            <span>Step 3 of 5 · Price Range</span>
            <span>42% complete</span>
          </div>
          <Progress value={42} />
        </div>

        {/* Header */}
        <div className="px-6 pb-6 text-center">
          <h1 className="text-2xl font-bold text-white leading-tight">
            What&apos;s your shopping budget<br />per item?
          </h1>
          <p className="text-slate-400 text-sm mt-2">
            We&apos;ll only recommend products within this price range
          </p>
        </div>

        {/* Price options */}
        <div className="flex-1 px-4 grid grid-cols-2 gap-3 max-w-lg mx-auto w-full pb-4">
          {PRICE_RANGES.map((range) => {
            const selected = selectedBudget === range.value;
            return (
              <button
                key={range.value}
                onClick={() => setSelectedBudget(range.value)}
                className={`relative rounded-2xl px-4 py-6 flex flex-col items-center justify-center text-center transition-all border-2 ${
                  selected
                    ? "border-violet-500 bg-violet-900/40 scale-[0.97]"
                    : "border-white/10 bg-white/5 hover:border-violet-400/50"
                }`}
              >
                {selected && (
                  <div className="absolute top-3 right-3 w-5 h-5 bg-violet-500 rounded-full flex items-center justify-center">
                    <span className="text-white text-xs font-bold">✓</span>
                  </div>
                )}
                <p className="text-white font-bold text-lg leading-tight">{range.label}</p>
                <p className="text-slate-400 text-xs mt-1">{range.sub}</p>
              </button>
            );
          })}
        </div>

        {/* Continue */}
        <div className="px-4 py-6 max-w-lg mx-auto w-full">
          <Button
            onClick={handleNext}
            disabled={!selectedBudget}
            className="w-full h-14 text-base font-semibold tracking-widest uppercase rounded-2xl bg-violet-600 hover:bg-violet-700 disabled:bg-slate-700 disabled:text-slate-500"
          >
            CONTINUE
          </Button>
        </div>
      </div>
    );
  }

  // ── Regular Question UI ────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-gradient-to-br from-violet-50 to-indigo-100">
      <div className="w-full max-w-lg mb-8">
        <div className="flex justify-between text-xs text-slate-500 mb-2">
          <span>Step 3 of 5 · Question {step - 1}/{QUESTIONS.length}</span>
          <span>{progress}% complete</span>
        </div>
        <Progress value={progress} />
      </div>

      <Card className="w-full max-w-lg shadow-xl">
        <CardHeader className="text-center">
          <CardTitle className="text-xl">{q!.title}</CardTitle>
          <CardDescription>{q!.subtitle}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className={`grid gap-3 ${q!.options.length > 4 ? "grid-cols-2" : "grid-cols-1"}`}>
            {q!.options.map((opt) => {
              const isSelected = q!.type === "multi"
                ? (current as string[] | undefined)?.includes(opt.value)
                : current === opt.value;
              return (
                <button
                  key={opt.value}
                  onClick={() => toggle(opt.value)}
                  className={`flex items-center gap-3 p-3 rounded-xl border-2 transition-all text-left ${
                    isSelected
                      ? "border-violet-500 bg-violet-50 shadow-md"
                      : "border-slate-200 hover:border-violet-300 hover:bg-violet-50/50"
                  }`}
                >
                  <span className="text-2xl">{opt.emoji}</span>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm text-slate-800">{opt.value}</p>
                    {opt.desc && <p className="text-xs text-slate-400 truncate">{opt.desc}</p>}
                  </div>
                  {isSelected && <span className="text-violet-600 text-sm">✓</span>}
                </button>
              );
            })}
          </div>

          {q!.type === "multi" && ((current as string[] | undefined)?.length ?? 0) > 0 && (
            <div className="flex flex-wrap gap-2 pt-2">
              {(current as string[]).map((v) => (
                <Badge key={v} variant="default">{v}</Badge>
              ))}
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <Button variant="outline" onClick={() => setStep((s) => (s - 1) as Step)}>← Back</Button>
            <Button
              onClick={handleNext}
              disabled={!canAdvanceQ || saving}
              className="flex-1"
            >
              {saving ? "Saving…" : step === QUESTIONS.length + 1 ? "Continue to Wardrobe →" : "Next →"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}


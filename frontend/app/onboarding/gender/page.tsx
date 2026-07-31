"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useUserStore } from "@/stores/user-store";
import { updatePreferences } from "@/lib/api";

const genders = [
  { value: "Male", emoji: "👨", label: "Male" },
  { value: "Female", emoji: "👩", label: "Female" },
  { value: "Others", emoji: "🧑", label: "Others / Prefer not to say" },
];

export default function GenderPage() {
  const router = useRouter();
  const { userId, setPreferences, preferences, setOnboardingStep } = useUserStore();
  const [selected, setSelected] = useState<string>(preferences.gender || "");
  const [loading, setLoading] = useState(false);

  const handleNext = async () => {
    if (!selected) return;
    setLoading(true);
    setPreferences({ gender: selected as "Male" | "Female" | "Others" });
    if (userId) {
      await updatePreferences(userId, { gender: selected }).catch(() => {});
    }
    setOnboardingStep(2);
    router.push("/onboarding/questionnaire");
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-gradient-to-br from-violet-50 to-indigo-100">
      <div className="w-full max-w-md mb-8">
        <div className="flex justify-between text-xs text-slate-500 mb-2">
          <span>Step 2 of 5</span>
          <span>40% complete</span>
        </div>
        <Progress value={40} />
      </div>

      <Card className="w-full max-w-md shadow-xl">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl">How do you identify?</CardTitle>
          <CardDescription>This helps us tailor outfit recommendations to your preferences</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {genders.map((g) => (
            <button
              key={g.value}
              onClick={() => setSelected(g.value)}
              className={`w-full flex items-center gap-4 p-4 rounded-2xl border-2 transition-all ${
                selected === g.value
                  ? "border-violet-500 bg-violet-50 shadow-md"
                  : "border-slate-200 hover:border-violet-300 hover:bg-violet-50/50"
              }`}
            >
              <span className="text-3xl">{g.emoji}</span>
              <span className="font-medium text-slate-800">{g.label}</span>
              {selected === g.value && <span className="ml-auto text-violet-600 text-xl">✓</span>}
            </button>
          ))}

          <Button
            onClick={handleNext}
            disabled={!selected || loading}
            className="w-full mt-2"
            size="lg"
          >
            {loading ? "Saving…" : "Continue →"}
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

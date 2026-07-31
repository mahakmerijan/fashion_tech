import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface FaceProfile {
  face_shape: string;
  skin_tone: string;
  eye_color: string;
  hair_color: string;
  expression_vibe: string;
  style_personality: string;
  color_season: string;
  dominant_face_color_hex?: string;
}

export interface Preferences {
  gender: "Male" | "Female" | "Others" | "";
  fit: string;
  favorite_colors: string[];
  footwear: string;
  pants: string;
  fabrics: string[];
  budget: string;
  style_personality: string;
  priority: string;
  experiment_level: number;
  climate: string;
  brand_preference: string;
  sustainability: boolean;
}

export interface WardrobeItem {
  item_id: string;
  image_url: string;
  category: string;
  sub_category?: string;
  primary_color: string;
  pattern?: string;
  estimated_fit?: string;
  ai_metadata?: Record<string, unknown>;
}

export interface UserState {
  userId: string | null;
  userEmail: string | null;
  authToken: string | null;
  selfieUrl: string | null;
  selfieFile: File | null;
  faceProfile: FaceProfile | null;
  preferences: Partial<Preferences>;
  wardrobe: WardrobeItem[];
  onboardingStep: number;

  setUserId: (id: string) => void;
  setAuth: (userId: string, token: string, email: string) => void;
  setSelfie: (url: string, file?: File) => void;
  setFaceProfile: (profile: FaceProfile) => void;
  setPreferences: (prefs: Partial<Preferences>) => void;
  addWardrobeItems: (items: WardrobeItem[]) => void;
  removeWardrobeItem: (itemId: string) => void;
  clearWardrobe: () => void;
  setOnboardingStep: (step: number) => void;
  reset: () => void;
}

const defaultPreferences: Partial<Preferences> = {
  gender: "",
  fit: "",
  favorite_colors: [],
  footwear: "",
  pants: "",
  fabrics: [],
  budget: "",
  style_personality: "",
  priority: "",
  experiment_level: 3,
  climate: "",
  brand_preference: "",
  sustainability: false,
};

export const useUserStore = create<UserState>()(
  persist(
    (set) => ({
      userId: null,
      userEmail: null,
      authToken: null,
      selfieUrl: null,
      selfieFile: null,
      faceProfile: null,
      preferences: defaultPreferences,
      wardrobe: [],
      onboardingStep: 0,

      setUserId: (id) => set({ userId: id }),
      setAuth: (userId, token, email) => set({ userId, authToken: token, userEmail: email }),
      setSelfie: (url, file) => set({ selfieUrl: url, selfieFile: file ?? null }),
      setFaceProfile: (profile) => set({ faceProfile: profile }),
      setPreferences: (prefs) =>
        set((s) => ({ preferences: { ...s.preferences, ...prefs } })),
      addWardrobeItems: (items) =>
        set((s) => ({ wardrobe: [...s.wardrobe, ...items] })),
      removeWardrobeItem: (itemId) =>
        set((s) => ({ wardrobe: s.wardrobe.filter((i) => i.item_id !== itemId) })),
      clearWardrobe: () => set({ wardrobe: [] }),
      setOnboardingStep: (step) => set({ onboardingStep: step }),
      reset: () =>
        set({
          userId: null,
          selfieUrl: null,
          selfieFile: null,
          faceProfile: null,
          preferences: defaultPreferences,
          wardrobe: [],
          onboardingStep: 0,
        }),
    }),
    { name: "fashion-tech-user", partialize: (s) => ({ userId: s.userId, userEmail: s.userEmail, authToken: s.authToken, faceProfile: s.faceProfile, preferences: s.preferences, wardrobe: s.wardrobe }),
      // Migrate old relative image URLs to absolute on rehydration
      merge: (persisted: unknown, current) => {
        const p = persisted as Partial<typeof current>;
        if (p?.wardrobe) {
          const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
          p.wardrobe = p.wardrobe.map((item) => ({
            ...item,
            image_url: item.image_url?.startsWith("http")
              ? item.image_url
              : `${API}${item.image_url}`,
          }));
        }
        return { ...current, ...p };
      },
    }
  )
);

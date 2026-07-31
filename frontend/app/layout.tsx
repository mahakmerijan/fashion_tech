import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "StyleAI – Your Personal Fashion Intelligence",
  description: "AI-powered outfit recommendations, wardrobe analysis, and virtual try-on.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#f8f7ff] text-[#1e1b4b] antialiased">
        {children}
      </body>
    </html>
  );
}

import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// The two typefaces from the Figma designs: Inter for prose, JetBrains Mono for UI chrome.
const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const jetbrainsMono = JetBrains_Mono({ variable: "--font-jetbrains-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "LectureLoop",
  description: "The lecture listens back.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#0e1a24" };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${jetbrainsMono.variable} min-h-dvh bg-ink font-ui text-fg antialiased`}>
        {children}
      </body>
    </html>
  );
}

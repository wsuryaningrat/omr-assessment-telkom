import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LJK Scanner v2 — Math Center",
  description: "Pemindai LJK profiling literasi numerik, Math Center CSWDA Telkom University",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#ba0c2f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="id">
      <body className="min-h-dvh">{children}</body>
    </html>
  );
}

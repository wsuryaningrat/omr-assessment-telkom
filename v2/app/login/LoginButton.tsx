"use client";
import { useState } from "react";
import { supabaseBrowser } from "@/lib/supabase/client";

export default function LoginButton({ next }: { next?: string }) {
  const [loading, setLoading] = useState(false);
  const onClick = async () => {
    setLoading(true);
    const origin = window.location.origin;
    const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    await supabaseBrowser().auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${origin}/auth/callback?next=${encodeURIComponent(safeNext)}`,
        queryParams: { prompt: "select_account" },
      },
    });
  };
  return (
    <button onClick={onClick} disabled={loading} className="btn-primary w-full py-3">
      <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden>
        <path fill="#fff" d="M21.35 11.1H12v2.98h5.35c-.23 1.5-1.66 4.4-5.35 4.4-3.22 0-5.85-2.67-5.85-5.96S8.78 6.56 12 6.56c1.83 0 3.06.78 3.76 1.45l2.57-2.47C16.68 4 14.55 3 12 3 6.98 3 2.92 7.03 2.92 12S6.98 21 12 21c6.93 0 9.53-4.86 9.53-9.36 0-.63-.07-1.1-.18-1.54Z" />
      </svg>
      {loading ? "Mengalihkan…" : "Masuk dengan Google"}
    </button>
  );
}

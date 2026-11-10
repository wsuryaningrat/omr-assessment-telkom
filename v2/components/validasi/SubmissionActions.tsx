"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { hapusSubmission } from "@/app/(app)/scan/actions";

/** Upload ulang (foto baru menggantikan yang ini) & hapus (admin) untuk satu LJK. */
export default function SubmissionActions({ id, kelasId, nim, isAdmin }: { id: string; kelasId: string; nim: string; isAdmin: boolean }) {
  const router = useRouter();
  const [err, setErr] = useState<string | null>(null);
  const [pending, start] = useTransition();
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Link href={`/scan/${encodeURIComponent(kelasId)}?ulang=${nim}`} className="btn-ghost px-3 py-1.5 text-xs" title="Foto ulang LJK ini; foto baru menggantikan yang lama">
        📷 Upload ulang
      </Link>
      {isAdmin && (
        <button
          type="button"
          disabled={pending}
          className="btn px-3 py-1.5 text-xs border border-bad-line bg-bad-bg text-bad hover:bg-bad hover:text-white"
          onClick={() => {
            if (!window.confirm(`Hapus LJK ${nim} beserta fotonya? Tindakan ini tidak bisa dibatalkan.`)) return;
            start(async () => {
              const r = await hapusSubmission(id);
              if (!r.ok) return setErr(r.msg);
              router.push(`/validasi/${encodeURIComponent(kelasId)}`);
            });
          }}
        >
          {pending ? "Menghapus…" : "🗑 Hapus"}
        </button>
      )}
      {err && <span className="text-xs text-bad">{err}</span>}
    </div>
  );
}

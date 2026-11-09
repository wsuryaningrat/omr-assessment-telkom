import Link from "next/link";
import { asset } from "@/lib/basePath";

/** Kepala halaman bergaya mathcenter.cswda.id: logo Math Center + judul, logo Telkom & CSWDA di kanan. */
export default function BrandBar({ children, subtitle = "Profiling Literasi Numerasi" }: { children?: React.ReactNode; subtitle?: string }) {
  return (
    <div className="flex items-center gap-3">
      <Link href="/" className="flex min-w-0 items-center gap-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={asset("/brand/mathcenter.png")} alt="Math Center" className="h-8 w-auto shrink-0" />
        <span className="min-w-0 border-l border-line pl-3 leading-tight">
          <span className="block truncate text-sm font-bold">Evaluasi LJK</span>
          <span className="block truncate text-xs text-ink2">{subtitle}</span>
        </span>
      </Link>
      <div className="ml-auto flex items-center gap-3">
        <span className="hidden items-center gap-3 lg:flex">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={asset("/brand/telkom.png")} alt="Telkom University" className="h-7 w-auto" />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={asset("/brand/cswda.png")} alt="CSWDA" className="h-6 w-auto" />
        </span>
        {children}
      </div>
    </div>
  );
}

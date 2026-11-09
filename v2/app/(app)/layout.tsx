import Link from "next/link";
import { requirePengawas } from "@/lib/auth";
import NavLinks from "@/components/NavLinks";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { pengawas } = await requirePengawas();
  const isAdmin = pengawas.role === "admin";
  return (
    <div className="min-h-dvh pb-20 md:pb-0">
      <header className="sticky top-0 z-30 border-b border-line bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-2.5">
          <Link href="/" className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand text-xs font-black text-white">MC</span>
            <span className="hidden text-sm font-bold sm:block">LJK Scanner v2</span>
          </Link>
          <nav className="hidden flex-1 md:block">
            <NavLinks isAdmin={isAdmin} variant="top" />
          </nav>
          <div className="ml-auto flex items-center gap-3 text-right">
            <div className="leading-tight">
              <div className="text-sm font-semibold">{pengawas.nama}</div>
              <div className="text-xs text-muted">{pengawas.email}</div>
            </div>
            <form action="/auth/signout" method="post">
              <button className="btn-ghost px-3 py-1.5 text-xs">Keluar</button>
            </form>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-5">{children}</main>
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-white md:hidden">
        <NavLinks isAdmin={isAdmin} variant="bottom" />
      </nav>
    </div>
  );
}

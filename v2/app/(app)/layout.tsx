import { requirePengawas } from "@/lib/auth";
import BrandBar from "@/components/BrandBar";
import NavLinks from "@/components/NavLinks";
import { asset } from "@/lib/basePath";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { pengawas } = await requirePengawas();
  const isAdmin = pengawas.role === "admin";
  return (
    <div className="min-h-dvh pb-20 md:pb-0">
      <header className="sticky top-0 z-30 border-b border-line bg-white/90 backdrop-blur-md">
        <div className="mx-auto max-w-6xl px-4 py-2.5">
          <BrandBar>
            <div className="hidden text-right leading-tight sm:block">
              <div className="text-sm font-semibold">{pengawas.nama}</div>
              <div className="text-xs text-ink2">{pengawas.email}</div>
            </div>
            <form action={asset("/auth/signout")} method="post">
              <button className="btn-ghost px-3 py-1.5 text-xs">Keluar</button>
            </form>
          </BrandBar>
        </div>
        <nav className="mx-auto hidden max-w-6xl px-4 pb-2 md:block">
          <NavLinks isAdmin={isAdmin} variant="top" />
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-5">{children}</main>
      <footer className="mx-auto max-w-6xl px-4 pt-4 pb-8 text-center text-xs text-ink3">
        developed by WHS MathCenter CSWDA Telkom University
      </footer>
      <nav className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden">
        <NavLinks isAdmin={isAdmin} variant="bottom" />
      </nav>
    </div>
  );
}

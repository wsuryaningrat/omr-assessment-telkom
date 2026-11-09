"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Beranda", icon: "◧" },
  { href: "/scan", label: "Scan LJK", icon: "◉" },
  { href: "/validasi", label: "Validasi", icon: "✓" },
  { href: "/rekap", label: "Rekap", icon: "▤", admin: true },
  { href: "/admin", label: "Admin", icon: "⚙", admin: true },
];

export default function NavLinks({ isAdmin, variant }: { isAdmin: boolean; variant: "top" | "bottom" }) {
  const path = usePathname();
  const links = LINKS.filter((l) => !l.admin || isAdmin);
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  if (variant === "top") {
    return (
      <ul className="flex gap-1">
        {links.map((l) => (
          <li key={l.href}>
            <Link
              href={l.href}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${active(l.href) ? "bg-brand-soft text-brand" : "text-ink2 hover:bg-soft"}`}
            >
              {l.label}
            </Link>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <ul className="flex">
      {links.map((l) => (
        <li key={l.href} className="flex-1">
          <Link
            href={l.href}
            className={`flex flex-col items-center py-2 text-[11px] font-semibold ${active(l.href) ? "text-brand" : "text-ink3"}`}
          >
            <span className="text-lg leading-none">{l.icon}</span>
            {l.label}
          </Link>
        </li>
      ))}
    </ul>
  );
}

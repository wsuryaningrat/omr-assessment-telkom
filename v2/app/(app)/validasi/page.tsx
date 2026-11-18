import { redirect } from "next/navigation";

/** Daftar kelas validasi sudah digabung ke menu Scan LJK. */
export default async function ValidasiIndex(props: { searchParams: Promise<{ semua?: string }> }) {
  const { semua } = await props.searchParams;
  redirect(semua ? "/scan" : "/scan?status=perlu");
}

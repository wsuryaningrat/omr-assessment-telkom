export interface RosterEntry {
  nim: string;
  nama: string;
}

export interface NimMatch {
  /** Mahasiswa yang cocok persis (semua digit terbaca & sama). */
  exact: RosterEntry | null;
  /** Kandidat terdekat (beda <= 2 digit, '?' dianggap wildcard), urut dari paling mirip. */
  suggestions: (RosterEntry & { diff: number })[];
}

/** Cocokkan NIM hasil OMR (boleh berisi '?') dengan daftar mahasiswa di kelas. */
export function matchNim(read: string, roster: RosterEntry[]): NimMatch {
  const exact = roster.find((r) => r.nim === read) ?? null;
  const suggestions = roster
    .map((r) => {
      if (r.nim.length !== read.length) return { ...r, diff: 99 };
      let diff = 0;
      for (let i = 0; i < read.length; i++) if (read[i] !== "?" && read[i] !== r.nim[i]) diff++;
      // '?' tetap dihitung setengah agar yang terbaca lengkap lebih diprioritaskan
      const unknown = [...read].filter((c) => c === "?").length;
      return { ...r, diff: diff + unknown * 0.5 };
    })
    .filter((r) => r.diff <= 2.5 && r.nim !== exact?.nim)
    .sort((a, b) => a.diff - b.diff)
    .slice(0, 5);
  return { exact, suggestions };
}

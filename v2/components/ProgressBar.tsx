export default function ProgressBar({ total, done, valid }: { total: number; done: number; valid: number }) {
  const pct = (n: number) => (total ? Math.min(100, (n / total) * 100) : 0);
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-soft" title={`${valid} valid · ${done} terupload · ${total} peserta`}>
      <div className="relative h-full">
        <div className="absolute inset-y-0 left-0 bg-amber-400" style={{ width: `${pct(done)}%` }} />
        <div className="absolute inset-y-0 left-0 bg-ok" style={{ width: `${pct(valid)}%` }} />
      </div>
    </div>
  );
}

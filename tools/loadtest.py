"""Uji beban: N pengawas paralel, masing-masing mengunggah M foto ke server.

    python tools/loadtest.py --url http://IP:8000 --users 35 --files 40 [--photo foto.jpg]

Tanpa --photo, dibuat foto JPEG ~4 MB dari LJK.pdf (sintetis). Untuk hasil paling
realistis, pakai --photo dengan foto LJK asli dari ponsel.
"""
import argparse
import asyncio
import io
import os
import statistics
import sys
import time

import httpx

VALID = {"nama_pengawas": "Uji Beban", "hp": "081234567890", "ruangan": "LOAD", "kelas": "UJI-01",
         "fakultas": "FIF - Fakultas Informatika", "prodi": "S1 Informatika"}


def synth_photo():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    import cv2
    from tests.regression.fixtures import build_cases
    ok, buf = cv2.imencode(".jpg", build_cases()["filled_phone"], [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes()


async def one_user(client, base, n_files, photo, idx, stats):
    t0 = time.time()
    r = await client.post(f"{base}/api/sessions", json=VALID)
    sid = r.json()["id"]
    files = [("files", (f"u{idx}_{i}.jpg", photo, "image/jpeg")) for i in range(n_files)]
    r = await client.post(f"{base}/api/sessions/{sid}/files", files=files)
    r.raise_for_status()
    t_upload = time.time() - t0
    while True:
        d = (await client.get(f"{base}/api/sessions/{sid}")).json()
        if not d["scanning"]:
            break
        await asyncio.sleep(1.0)
    stats.append({"upload": t_upload, "total": time.time() - t0,
                  "lembar": d["summary"]["lembar"], "gagal": len(d["files"]["failed"])})


async def main(a):
    photo = open(a.photo, "rb").read() if a.photo else synth_photo()
    print(f"foto: {len(photo)/1e6:.1f} MB | {a.users} user x {a.files} berkas = {a.users*a.files} lembar")
    stats = []
    limits = httpx.Limits(max_connections=a.users * 2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(600), limits=limits) as client:
        t0 = time.time()
        await asyncio.gather(*(one_user(client, a.url, a.files, photo, i, stats) for i in range(a.users)))
        wall = time.time() - t0
    tot = [s["total"] for s in stats]
    print(f"selesai dalam {wall:.0f} dtk | lembar terbaca {sum(s['lembar'] for s in stats)} | berkas gagal {sum(s['gagal'] for s in stats)}")
    print(f"upload per user: median {statistics.median(s['upload'] for s in stats):.1f} dtk")
    print(f"waktu tunggu per user: median {statistics.median(tot):.0f} dtk | terlama {max(tot):.0f} dtk")
    print(f"throughput: {sum(s['lembar'] for s in stats)/wall:.2f} lembar/dtk")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--users", type=int, default=35)
    p.add_argument("--files", type=int, default=40)
    p.add_argument("--photo")
    asyncio.run(main(p.parse_args()))

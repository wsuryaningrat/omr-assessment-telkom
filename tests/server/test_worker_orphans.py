"""Pekerja pemindai tidak boleh menjadi yatim: bila proses server mati paksa, pekerja harus ikut berhenti."""
import os
import signal
import subprocess
import sys
import textwrap
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")

PARENT = textwrap.dedent('''
    import os, sys
    from concurrent.futures import ProcessPoolExecutor
    from server import worker
    if __name__ == "__main__":
        pool = ProcessPoolExecutor(max_workers=2, initializer=worker.init_worker, initargs=(os.getpid(),))
        pids = list(pool.map(worker.warmup, range(2)))
        print("WORKERS", *pids, flush=True)
        sys.stdin.read()            # tunggu dimatikan paksa oleh tes
''')


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class TestNoOrphans(unittest.TestCase):
    def test_workers_exit_when_parent_is_killed(self):
        p = subprocess.Popen([sys.executable, "-c", PARENT], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            line = ""
            while not line.startswith("WORKERS"):
                line = p.stdout.readline()
                self.assertTrue(line, "induk berhenti sebelum melaporkan pekerja")
            pids = [int(x) for x in line.split()[1:]]
            self.assertTrue(all(alive(x) for x in pids))
            os.kill(p.pid, signal.SIGKILL)                      # induk dimatikan paksa (tanpa kesempatan bersih-bersih)
            p.wait(timeout=10)
            deadline = time.time() + 10
            while time.time() < deadline and any(alive(x) for x in pids):
                time.sleep(0.3)
            self.assertFalse([x for x in pids if alive(x)], "pekerja masih hidup setelah induk mati (yatim)")
        finally:
            if p.poll() is None:
                p.kill()


if __name__ == "__main__":
    unittest.main()

"""Title/subtitle of every MCU/board symbol must sit inside the body and never overlap pin names (real browser bboxes)."""
import os, shutil, socket, subprocess, sys, time, pathlib, pytest
pw = pytest.importorskip("playwright")
CHROME = "/usr/bin/google-chrome"
ROOT = pathlib.Path(__file__).resolve().parents[1]

@pytest.mark.skipif(not os.path.exists(CHROME), reason="needs chrome")
def test_mcu_title_never_overlaps_pin_names(tmp_path):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    srv = subprocess.Popen([sys.executable, "-m", "chipllama", "serve", "--host", "127.0.0.1", "--port", str(port), "--no-browser"], cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try: socket.create_connection(("127.0.0.1", port), 0.5).close(); break
            except OSError: time.sleep(0.25)
        r = subprocess.run([sys.executable, str(ROOT / "scripts/check_mcu_titles.py")], env=dict(env, CL_URL=f"http://127.0.0.1:{port}"), capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    finally:
        srv.kill()

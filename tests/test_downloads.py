"""The model downloader resumes stalled, crawling and dropped transfers and verifies what it got."""

import hashlib
import os
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from backend import models

DATA = os.urandom(2 * 1024 * 1024)
SHA = hashlib.sha256(DATA).hexdigest()


class Handler(BaseHTTPRequestHandler):
    hits: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        mode = self.path.strip("/")
        n = self.hits[mode] = self.hits.get(mode, 0) + 1
        if mode == "missing":
            self.send_response(404)
            self.end_headers()
            return
        start = int(self.headers["Range"].split("=")[1].split("-")[0]) if self.headers.get("Range") and mode != "norange" else 0
        body = (DATA if mode != "bad" else bytes(len(DATA)))[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            if n == 1 and mode in ("stall", "norange", "bad"):
                self.wfile.write(body[: len(body) // 2])
                self.wfile.flush()
                time.sleep(5)  # hangs half-way
                return
            if n == 1 and mode == "trickle":
                for i in range(60):
                    self.wfile.write(body[i * 100 : i * 100 + 100])
                    self.wfile.flush()
                    time.sleep(0.1)  # about 1 KB/s
                return
            if mode == "dead":
                time.sleep(4)
                return
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(models, "STALL_SECONDS", 1)
    monkeypatch.setattr(models, "MIN_RATE", 50_000)
    monkeypatch.setattr(models, "MAX_FRUITLESS_ATTEMPTS", 3)
    monkeypatch.setattr(models, "time", types.SimpleNamespace(time=time.time, sleep=lambda s: None))


@pytest.mark.parametrize("mode", ["stall", "trickle", "norange"])
def test_recovers_and_verifies(server, tmp_path, mode):
    job = models.Download("x/y", tmp_path)
    target = tmp_path / f"{mode}.bin"
    models._fetch(job, f"{server}/{mode}", target, len(DATA), SHA)
    assert target.read_bytes() == DATA and job.retries == 1 and not (tmp_path / f"{mode}.bin.part").exists()


def test_corrupt_download_is_deleted(server, tmp_path):
    folder = tmp_path / "dl"
    with pytest.raises(ValueError, match="校验失败"):
        models._fetch(models.Download("x/y", folder), f"{server}/bad", folder / "bad.bin", len(DATA), SHA)
    assert not list(folder.iterdir())


def test_missing_file_fails_at_once(server, tmp_path):
    job = models.Download("x/y", tmp_path)
    with pytest.raises(ValueError, match="HTTP 404"):
        models._fetch(job, f"{server}/missing", tmp_path / "m.bin", len(DATA), SHA)
    assert job.retries == 0


def test_dead_server_gives_up_and_keeps_the_partial_file(server, tmp_path):
    with pytest.raises(ValueError, match="没有进展"):
        models._fetch(models.Download("x/y", tmp_path), f"{server}/dead", tmp_path / "d.bin", len(DATA), SHA)
    assert (tmp_path / "d.bin.part").exists()


def test_resumes_an_earlier_partial_file_and_skips_finished_ones(server, tmp_path):
    (tmp_path / "r.bin.part").write_bytes(DATA[:1_000_000])
    job = models.Download("x/y", tmp_path)
    models._fetch(job, f"{server}/resume", tmp_path / "r.bin", len(DATA), SHA)
    assert (tmp_path / "r.bin").read_bytes() == DATA
    before = Handler.hits.get("resume")
    models._fetch(job, f"{server}/resume", tmp_path / "r.bin", len(DATA), SHA)
    assert Handler.hits.get("resume") == before

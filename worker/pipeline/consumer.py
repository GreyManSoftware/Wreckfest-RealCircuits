"""Queue consumer: the worker container's default command (`entrypoint worker`).

Env vars:
    STORAGE_CONNECTION_STRING  Azure Storage connection string. "UseDevelopmentStorage=true[;DevelopmentStorageProxyUri=
                               http://azurite]" (the API's Azurite form) is expanded to Azurite's well-known account.
    API_BASE_URL               e.g. http://api:8080
    WORKER_SECRET              bearer secret for PATCH /api/internal/builds/{id} (the API's Worker:Secret)
    QUEUE_NAME (builds), VISIBILITY_TIMEOUT_S (1800), HEARTBEAT_S (300), POLL_S (5), MAX_ATTEMPTS (3)

Loop (one build at a time): receive one message (visibility 30 min) -> PATCH running -> download the spec and
any .tcat uploads -> run the pipeline in a temp dir, re-sending `running` and extending the message visibility
every 5 min -> upload artifacts/{buildId}/{slug}.zip, build.log, build-report.json -> PATCH succeeded/failed
-> delete the message. A 404/409 from the API (unknown or already finished build) drops the message.
SIGTERM/SIGINT: exit when idle; during a build, cancel it and make the message visible again for a retry.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from common import BuildLog, Cancelled  # noqa: E402

# Azurite's documented development account key (public, the same for every Azurite install; not a secret).
AZURITE_ACCOUNT = "devstoreaccount1"
AZURITE_KEY = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="

QUEUE = os.environ.get("QUEUE_NAME", "builds")
VISIBILITY = int(os.environ.get("VISIBILITY_TIMEOUT_S", "1800"))
HEARTBEAT = int(os.environ.get("HEARTBEAT_S", "300"))
POLL = float(os.environ.get("POLL_S", "5"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
SPECS, ARTIFACTS, TCATS = "specs", "artifacts", "tcats"


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} worker: {msg}", flush=True)


def connection_string(raw: str) -> str:
    parts = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
    if parts.get("UseDevelopmentStorage", "").lower() != "true":
        return raw
    host = parts.get("DevelopmentStorageProxyUri", "http://127.0.0.1").rstrip("/")
    return (f"DefaultEndpointsProtocol=http;AccountName={AZURITE_ACCOUNT};AccountKey={AZURITE_KEY};"
            f"BlobEndpoint={host}:10000/{AZURITE_ACCOUNT};QueueEndpoint={host}:10001/{AZURITE_ACCOUNT};")


class Api:
    def __init__(self, base: str, secret: str) -> None:
        self.base, self.secret = base.rstrip("/"), secret

    def patch(self, build_id: str, status: str, **extra) -> int:
        """PATCH the build status. Returns the HTTP status (0 when the API is unreachable)."""
        body = {"status": status, **{k: v for k, v in extra.items() if v}}
        req = urllib.request.Request(f"{self.base}/api/internal/builds/{build_id}", method="PATCH",
                                     data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {self.secret}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
            log(f"PATCH {status} {build_id}: HTTP {code} {e.read()[:300]!r}")
        except OSError as e:
            log(f"PATCH {status} {build_id}: {e}")
            return 0
        if code < 300:
            log(f"PATCH {status} {build_id}: {code}")
        return code


class Consumer:
    def __init__(self) -> None:
        from azure.storage.blob import BlobServiceClient
        from azure.storage.queue import QueueClient

        missing = [k for k in ("STORAGE_CONNECTION_STRING", "API_BASE_URL", "WORKER_SECRET") if not os.environ.get(k)]
        if missing:
            raise SystemExit(f"worker: missing env vars: {', '.join(missing)}")
        conn = connection_string(os.environ["STORAGE_CONNECTION_STRING"])
        self.queue = QueueClient.from_connection_string(conn, QUEUE)
        self.blobs = BlobServiceClient.from_connection_string(conn)
        self.api = Api(os.environ["API_BASE_URL"], os.environ["WORKER_SECRET"])
        self.stop = threading.Event()
        self.work_root = os.environ.get("PIPELINE_WORK", os.path.expanduser("~/work"))

    def _ensure(self) -> None:
        from azure.core.exceptions import ResourceExistsError

        for create in (self.queue.create_queue, lambda: self.blobs.create_container(ARTIFACTS)):
            try:
                create()
            except ResourceExistsError:
                pass

    def run(self) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        log(f"queue '{QUEUE}', API {self.api.base}, visibility {VISIBILITY} s, heartbeat {HEARTBEAT} s")
        ready = False
        while not self.stop.is_set():
            try:
                if not ready:
                    self._ensure()
                    ready = True
                    log("connected; waiting for builds")
                msg = self.queue.receive_message(visibility_timeout=VISIBILITY)
            except Exception as e:  # noqa: BLE001  (storage not up yet, network blips)
                log(f"queue error: {type(e).__name__}: {str(e)[:200]}")
                self.stop.wait(POLL)
                continue
            if msg is None:
                self.stop.wait(POLL)
                continue
            try:
                self.handle(msg)
            except Exception as e:  # noqa: BLE001
                log(f"unexpected error handling message {msg.id}: {type(e).__name__}: {e}")
        log("stopped")
        return 0

    def _delete(self, msg) -> None:
        try:
            self.queue.delete_message(msg)
        except Exception as e:  # noqa: BLE001
            log(f"delete message failed: {e}")

    def handle(self, msg) -> None:
        try:
            job = json.loads(msg.content)
            build_id, track_id = str(job["buildId"]), str(job["trackId"])
            spec_blob = job.get("specBlobName") or f"{build_id}.json"
        except (ValueError, KeyError, TypeError) as e:
            log(f"dropping malformed message {msg.id}: {e}: {str(msg.content)[:200]!r}")
            self._delete(msg)
            return
        log(f"build {build_id} (track {track_id} v{job.get('version')}), attempt {msg.dequeue_count}")
        if msg.dequeue_count > MAX_ATTEMPTS:
            self.api.patch(build_id, "failed", errorMessage=f"worker gave up after {MAX_ATTEMPTS} attempts")
            self._delete(msg)
            return
        code = self.api.patch(build_id, "running")
        if code in (404, 409):
            log(f"build {build_id} is unknown or already finished (HTTP {code}); dropping the message")
            self._delete(msg)
            return
        if code == 0 or code >= 300:
            log("API not ready; the message will be retried")
            self.queue.update_message(msg, visibility_timeout=30)
            return

        work = tempfile.mkdtemp(prefix=f"build-{build_id[:8]}-", dir=self._mkdir(self.work_root))
        try:
            self._build(msg, build_id, track_id, spec_blob, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    @staticmethod
    def _mkdir(path: str) -> str:
        os.makedirs(path, exist_ok=True)
        return path

    def _download(self, container: str, name: str, path: str) -> None:
        with open(path, "wb") as f:
            self.blobs.get_blob_client(container, name).download_blob().readinto(f)

    def _tcats(self, track_id: str, folder: str) -> dict[str, str]:
        from azure.core.exceptions import ResourceNotFoundError

        out = {}
        try:
            for b in self.blobs.get_container_client(TCATS).list_blobs(name_starts_with=f"{track_id}/"):
                if b.name.endswith(".tcat"):
                    layout = b.name.rsplit("/", 1)[-1][:-5]
                    path = os.path.join(folder, f"{layout}.tcat")
                    self._download(TCATS, b.name, path)
                    out[layout] = path
        except ResourceNotFoundError:
            pass
        return out

    def _upload(self, name: str, path: str, content_type: str) -> str:
        from azure.storage.blob import ContentSettings

        with open(path, "rb") as f:
            self.blobs.get_blob_client(ARTIFACTS, name).upload_blob(
                f, overwrite=True, content_settings=ContentSettings(content_type=content_type))
        return name

    def _build(self, msg, build_id: str, track_id: str, spec_blob: str, work: str) -> None:
        import pipeline

        out = os.path.join(work, "out")
        os.makedirs(os.path.join(work, "in"), exist_ok=True)
        blog = BuildLog()
        spec_path = os.path.join(work, "in", "spec.json")
        result: dict = {}
        try:
            self._download(SPECS, spec_blob, spec_path)
            tcats = self._tcats(track_id, os.path.join(work, "in"))
            if tcats:
                log(f"tcat uploads: {', '.join(sorted(tcats))}")
        except Exception as e:  # noqa: BLE001
            os.makedirs(out, exist_ok=True)
            blog.error(f"download: {type(e).__name__}: {e}")
            with open(os.path.join(out, "build.log"), "w", encoding="utf-8") as f:
                f.write(blog.render())
            result = {"ok": False, "error": f"could not download the spec: {type(e).__name__}",
                      "log": os.path.join(out, "build.log")}
            tcats = None

        cancel = threading.Event()
        if tcats is not None:
            def target():
                try:
                    result.update(pipeline.build_mod(spec_path, out, tcats, os.path.join(work, "pipeline"),
                                                     cancel=cancel, log=blog))
                except Cancelled:
                    result["cancelled"] = True

            t = threading.Thread(target=target, name=f"build-{build_id[:8]}")
            t.start()
            last = time.monotonic()
            while t.is_alive():
                t.join(1.0)
                if self.stop.is_set() and not cancel.is_set():
                    log("SIGTERM during a build: cancelling it; the message becomes visible again")
                    cancel.set()
                if time.monotonic() - last >= HEARTBEAT and not cancel.is_set():
                    last = time.monotonic()
                    self.api.patch(build_id, "running")
                    try:
                        msg = self.queue.update_message(msg, visibility_timeout=VISIBILITY)
                    except Exception as e:  # noqa: BLE001
                        log(f"could not extend the message visibility: {e}")
            if result.get("cancelled"):
                try:
                    self.queue.update_message(msg, visibility_timeout=0)
                except Exception:  # noqa: BLE001
                    pass
                return

        log_blob = artifact_blob = None
        try:
            if result.get("log") and os.path.isfile(result["log"]):
                log_blob = self._upload(f"{build_id}/build.log", result["log"], "text/plain; charset=utf-8")
            if result.get("report") and os.path.isfile(result["report"]):
                self._upload(f"{build_id}/build-report.json", result["report"], "application/json")
            if result.get("ok"):
                artifact_blob = self._upload(f"{build_id}/{result['slug']}.zip", result["zip"], "application/zip")
        except Exception as e:  # noqa: BLE001
            log(f"upload failed: {type(e).__name__}: {e}")
            result = {**result, "ok": False, "error": f"artifact upload failed: {type(e).__name__}"}

        if result.get("ok") and artifact_blob:
            code = self.api.patch(build_id, "succeeded", logBlobName=log_blob, artifactBlobName=artifact_blob)
        else:
            code = self.api.patch(build_id, "failed", logBlobName=log_blob,
                                  errorMessage=(result.get("error") or "build failed")[:1000])
        if code == 0 or (code >= 300 and code not in (404, 409)):
            # Couldn't report: leave the message so a later attempt reports (the build reruns).
            log(f"final status not reported (HTTP {code}); the message will be retried")
            return
        self._delete(msg)
        log(f"build {build_id} {'succeeded' if result.get('ok') else 'failed'}: {result.get('error') or result.get('zip')}")


def main() -> int:
    return Consumer().run()


if __name__ == "__main__":
    sys.exit(main())

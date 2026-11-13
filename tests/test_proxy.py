import http.client
import json
import threading
import time
import unittest
import uuid

from gpu_lease.config import from_text
from gpu_lease.proxy import STATUS_PATH, Engine
from tests.mocks import comfy_mock, free_port, llama_mock


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.llm, self.state = llama_mock(load_seconds=0.1, chat_seconds=0.0)
        self.comfy, self.jobs = comfy_mock(job_seconds=0.5)
        self.llm_port, self.comfy_port = free_port(), free_port()
        self.lock_name = "gpu-lease-test-%s" % uuid.uuid4().hex[:8]
        self.engine = None

    def tearDown(self):
        if self.engine is not None:
            self.engine.close()
        self.llm.close()
        self.comfy.close()

    def start(self, llm_token="", **lease):
        doc = {
            "lease": dict({"name": self.lock_name, "queue_timeout": 3,
                           "mode": "queue", "early_headers_after": 30,
                           "keepalive": 1, "grace": 2}, **lease),
            "backend": [
                {"name": "llm", "kind": "llama", "token": llm_token,
                 "listen": "127.0.0.1:%d" % self.llm_port,
                 "upstream": self.llm.url},
                {"name": "comfy", "kind": "comfyui",
                 "listen": "127.0.0.1:%d" % self.comfy_port,
                 "upstream": self.comfy.url,
                 "watch": {"timeout": 5, "poll": 0.05, "give_up_polls": 3}},
            ],
        }
        self.engine = Engine(from_text(doc), level="quiet").start()

    # -- helpers -----------------------------------------------------------
    def request(self, port, path, doc=None, headers=None, method="POST",
                timeout=10):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        body = json.dumps(doc).encode() if doc is not None else None
        head = {"Content-Type": "application/json"}
        head.update(headers or {})
        try:
            conn.request(method, path, body=body, headers=head)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data, dict(resp.getheaders())
        finally:
            conn.close()

    def post(self, port, path, doc=None, headers=None):
        started = time.monotonic()
        status, data, extra = self.request(port, path, doc, headers)
        return status, data, time.monotonic() - started, extra

    # -- tests -------------------------------------------------------------
    def test_turn_evicts_the_other_side_then_prepares_itself(self):
        self.start()
        self.state["m1"] = "loaded"                    # llama is holding VRAM
        status, _data, _secs, _h = self.post(self.comfy_port, "/prompt",
                                            {"prompt": {}})
        self.assertEqual(status, 200)
        self.assertTrue(wait_until(lambda: self.engine.leases.held_by() is None, 5))
        self.assertGreaterEqual(self.llm.count("/models/unload"), 1)
        self.assertEqual(self.state["m1"], "unloaded")

        status, _data, _secs, _h = self.post(
            self.llm_port, "/v1/chat/completions", {"model": "m1", "stream": False})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(self.comfy.count("/free"), 1)
        self.assertEqual(self.state["m1"], "loaded")
        self.assertGreaterEqual(self.llm.count("/models/load"), 1)

    def test_turns_never_overlap(self):
        self.llm, self.state = llama_mock(load_seconds=0.0, chat_seconds=0.5)
        self.start()
        finished = {}

        def chat():
            self.post(self.llm_port, "/v1/chat/completions",
                      {"model": "m1", "stream": False})
            finished["chat"] = time.monotonic()

        thread = threading.Thread(target=chat)
        thread.start()
        time.sleep(0.15)
        status, _data, elapsed, _h = self.post(self.comfy_port, "/prompt",
                                               {"prompt": {}})
        finished["comfy"] = time.monotonic()
        thread.join(10)
        self.assertEqual(status, 200)
        self.assertGreater(elapsed, 0.1)              # it waited for the chat
        self.assertGreater(finished["comfy"], finished["chat"])

    def test_watch_holds_the_turn_past_the_response(self):
        self.start()
        status, _data, elapsed, _h = self.post(self.comfy_port, "/prompt",
                                               {"prompt": {}})
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 0.4)                 # /prompt answers at once
        self.assertEqual(self.engine.leases.held_by(), "comfy")

        started = time.monotonic()
        status, _data, _extra = self.request(self.comfy_port, "/queue",
                                             method="GET")
        self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - started, 0.3)   # not gated
        self.assertEqual(self.engine.leases.held_by(), "comfy")

        self.assertTrue(wait_until(lambda: self.engine.leases.held_by() is None, 5))
        self.assertGreaterEqual(self.comfy.count("/history/p1"), 1)

    def test_ungated_post_does_not_take_the_lease(self):
        self.start()
        status, _data, _secs, _h = self.post(self.comfy_port, "/upload/image",
                                             {"name": "ref.png"})
        self.assertEqual(status, 200)
        self.assertIsNone(self.engine.status()["owner"])
        self.assertEqual(self.comfy.count("/upload/image"), 1)

    def test_reject_mode_answers_429_with_retry_after(self):
        self.llm, self.state = llama_mock(load_seconds=0.0, chat_seconds=0.6)
        self.start(mode="reject")
        thread = threading.Thread(
            target=self.post,
            args=(self.llm_port, "/v1/chat/completions"),
            kwargs={"doc": {"model": "m1", "stream": False}})
        thread.start()
        time.sleep(0.1)
        status, data, elapsed, extra = self.post(self.comfy_port, "/prompt",
                                                 {"prompt": {}})
        thread.join(10)
        self.assertEqual(status, 429)
        self.assertLess(elapsed, 0.3)
        self.assertEqual(extra.get("Retry-After"), "1")
        self.assertIn("gpu_busy", data.decode())

    def test_token_is_required_when_configured(self):
        self.start(llm_token="sekrit")
        status, _data, _secs, _h = self.post(
            self.llm_port, "/v1/chat/completions",
            {"model": "m1", "stream": False})
        self.assertEqual(status, 401)
        status, _data, _secs, _h = self.post(
            self.llm_port, "/v1/chat/completions",
            {"model": "m1", "stream": False},
            headers={"Authorization": "Bearer sekrit"})
        self.assertEqual(status, 200)

    def test_early_headers_keep_the_client_alive_while_waiting(self):
        self.comfy, self.jobs = comfy_mock(job_seconds=2.0)
        self.start(early_headers_after=0.05, keepalive=0.05)
        self.post(self.comfy_port, "/prompt", {"prompt": {}})
        self.assertEqual(self.engine.leases.held_by(), "comfy")

        conn = http.client.HTTPConnection("127.0.0.1", self.llm_port, timeout=10)
        conn.request("POST", "/v1/chat/completions",
                     body=json.dumps({"model": "m1", "stream": True}).encode(),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)            # headers before the turn
        seen = b""
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and b"[DONE]" not in seen:
            seen += resp.read1(4096) or b""
        conn.close()
        self.assertIn(b"gpu-lease waiting", seen)     # legal SSE comment filler
        self.assertIn(b"[DONE]", seen)

    def test_status_endpoint(self):
        self.start()
        status, data, _extra = self.request(self.llm_port, STATUS_PATH,
                                            method="GET")
        self.assertEqual(status, 200)
        doc = json.loads(data)
        self.assertEqual(doc["mode"], "queue")
        self.assertEqual([b["name"] for b in doc["backends"]], ["llm", "comfy"])
        self.assertIsNone(doc["owner"])

    def test_upstream_down_is_503_not_a_hang(self):
        self.start(grace=0)
        self.comfy.close()                            # the server "did not start"
        status, _data, elapsed, _h = self.post(self.comfy_port, "/prompt",
                                               {"prompt": {}})
        self.assertEqual(status, 503)
        self.assertLess(elapsed, 3)


if __name__ == "__main__":
    unittest.main()

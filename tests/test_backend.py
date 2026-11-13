import time
import unittest

from gpu_lease.backend import (Hook, Watch, build, evict, job_id_from,
                               llama_load, llama_models, prepare, watch_done)
from tests.mocks import (comfy_mock, koboldcpp_mock, llama_mock,
                         lmstudio_mock, ollama_mock)


class TestGating(unittest.TestCase):
    def test_llama_matches_every_model_running_path(self):
        backend = build("llm", ("127.0.0.1", 1), "http://127.0.0.1:2", "llama")
        for path in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings",
                     "/embedding", "/v1/rerank", "/rerank", "/infill",
                     "/v1/responses", "/api/chat/completions/"):
            self.assertTrue(backend.gates("POST", path), path)
        for path in ("/v1/models", "/props", "/slots", "/tokenize", "/health"):
            self.assertFalse(backend.gates("POST", path), path)
        self.assertFalse(backend.gates("GET", "/v1/chat/completions"))

    def test_comfyui_matches_only_prompt(self):
        backend = build("c", ("127.0.0.1", 1), "http://127.0.0.1:2", "comfyui")
        self.assertTrue(backend.gates("POST", "/prompt"))
        self.assertTrue(backend.gates("POST", "/api/prompt"))
        for path in ("/upload/image", "/history/1", "/queue", "/free", "/view"):
            self.assertFalse(backend.gates("POST", path), path)

    def test_generic_backend_gates_nothing_by_default(self):
        backend = build("g", ("127.0.0.1", 1), "http://127.0.0.1:2", "generic")
        self.assertFalse(backend.gates("POST", "/anything"))


class TestHooks(unittest.TestCase):
    def test_llama_load_does_not_return_before_loaded(self):
        mock, state = llama_mock(load_seconds=0.35)
        backend = build("l", ("127.0.0.1", 1), mock.url, "llama")
        try:
            started = time.monotonic()
            prepare(backend, {"model": "m1"})
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.3)
            self.assertEqual(state["m1"], "loaded")
            self.assertGreaterEqual(mock.count("/models/load"), 1)
        finally:
            mock.close()

    def test_llama_load_frees_the_single_slot_first(self):
        """--models-max 1: a sleeping preset still blocks the next load."""
        mock, state = llama_mock(load_seconds=0.05)
        state["m1"] = "sleeping"
        backend = build("l", ("127.0.0.1", 1), mock.url, "llama")
        try:
            prepare(backend, {"model": "m2"})
            self.assertEqual(state["m1"], "unloaded")
            self.assertEqual(state["m2"], "loaded")
            self.assertIn("/models/unload", " ".join(mock.paths()))
        finally:
            mock.close()

    def test_llama_evict_unloads_everything_resident(self):
        mock, state = llama_mock()
        state.update({"m1": "loaded", "m2": "sleeping"})
        backend = build("l", ("127.0.0.1", 1), mock.url, "llama")
        try:
            evict(backend)
            self.assertEqual(llama_models(mock.url), {"m1": "unloaded",
                                                      "m2": "unloaded"})
        finally:
            mock.close()

    def test_comfyui_evict_posts_free(self):
        mock, _jobs = comfy_mock()
        backend = build("c", ("127.0.0.1", 1), mock.url, "comfyui")
        try:
            evict(backend)
            self.assertEqual(mock.count("/free"), 1)
            body = [c[2] for c in mock.calls if c[1] == "/free"][0]
            self.assertEqual(body, {"unload_models": True, "free_memory": True})
        finally:
            mock.close()

    def test_prepare_skips_models_the_router_does_not_know(self):
        mock, state = llama_mock()
        backend = build("l", ("127.0.0.1", 1), mock.url, "llama")
        try:
            prepare(backend, {"model": "some/gguf/file.gguf"})
            self.assertEqual(mock.count("/models/load"), 0)
        finally:
            mock.close()

    def test_custom_http_hook_can_be_conditional(self):
        mock, _jobs = comfy_mock()
        backend = build("g", ("127.0.0.1", 1), mock.url, "generic",
                        evict_hook=Hook(kind="http", path="/free", method="POST",
                                        body={"unload_models": True},
                                        expect=(200,)))
        try:
            evict(backend)
            self.assertEqual(mock.count("/free"), 1)
        finally:
            mock.close()


class TestWatch(unittest.TestCase):
    def test_watch_waits_for_history_then_reports_done(self):
        mock, _jobs = comfy_mock(job_seconds=0.4)
        watch = Watch(path="/history/{id}", timeout=5, poll=0.05)
        try:
            import json
            import urllib.request
            req = urllib.request.Request(mock.url + "/prompt", data=b"{}",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = json.loads(resp.read())
            job = job_id_from(build("c", ("127.0.0.1", 1), mock.url, "comfyui"),
                              payload)
            self.assertEqual(job, "p1")
            started = time.monotonic()
            self.assertTrue(watch_done(watch, mock.url, job))
            self.assertGreaterEqual(time.monotonic() - started, 0.3)
        finally:
            mock.close()

    def test_watch_gives_up_on_a_cancelled_job(self):
        mock, _jobs = comfy_mock(job_seconds=60)
        watch = Watch(path="/history/{id}", timeout=30, poll=0.02,
                      give_up_polls=3)
        try:
            started = time.monotonic()
            done = watch_done(watch, mock.url, "gone", give_up_seen=lambda _j: False)
            self.assertFalse(done)
            self.assertLess(time.monotonic() - started, 5)
        finally:
            mock.close()


if __name__ == "__main__":
    unittest.main()

class TestOtherServers(unittest.TestCase):
    """Presets built from vendor docs (untested against the real programs),
    so these lock down the call sequence, not the vendored behaviour."""

    def test_ollama_gates_native_and_openai_paths(self):
        backend = build("o", ("127.0.0.1", 1), "http://127.0.0.1:2", "ollama")
        for path in ("/v1/chat/completions", "/v1/embeddings", "/api/chat",
                     "/api/generate", "/api/embed"):
            self.assertTrue(backend.gates("POST", path), path)
        for path in ("/api/ps", "/api/tags", "/api/version"):
            self.assertFalse(backend.gates("POST", path), path)

    def test_ollama_evict_unloads_every_resident_model(self):
        mock, resident = ollama_mock(models=("m1", "m2"))
        backend = build("o", ("127.0.0.1", 1), mock.url, "ollama")
        try:
            text = evict(backend)
            self.assertEqual(sorted(resident), [])
            self.assertEqual(mock.count("/api/generate"), 2)
            self.assertIn("m1", text)
        finally:
            mock.close()

    def test_ollama_embedding_model_falls_back_to_embed_route(self):
        mock, resident = ollama_mock(models=("embed:gemma",))
        backend = build("o", ("127.0.0.1", 1), mock.url, "ollama")
        try:
            evict(backend)
            self.assertEqual(sorted(resident), [])
            self.assertEqual(mock.count("/api/generate"), 1)   # refused
            self.assertGreaterEqual(mock.count("/api/embed"), 1)
        finally:
            mock.close()

    def test_lmstudio_evict_unloads_only_loaded_instances(self):
        mock, state = lmstudio_mock()
        backend = build("l", ("127.0.0.1", 1), mock.url, "lmstudio")
        try:
            evict(backend)
            self.assertEqual(state["openai/gpt-oss-20b"], "not-loaded")
            self.assertEqual(mock.count("/api/v1/models/unload"), 1)
        finally:
            mock.close()

    def test_koboldcpp_evict_uses_an_admin_route(self):
        mock, state = koboldcpp_mock()
        backend = build("k", ("127.0.0.1", 1), mock.url, "koboldcpp")
        try:
            evict(backend)
            self.assertEqual(state["model"], "")
            self.assertEqual(mock.count("/api/admin/unload"), 1)
        finally:
            mock.close()

    def test_koboldcpp_without_admin_explains_itself(self):
        mock, state = koboldcpp_mock(admin_route=None)
        backend = build("k", ("127.0.0.1", 1), mock.url, "koboldcpp")
        try:
            with self.assertRaises(RuntimeError) as ctx:
                evict(backend)
            self.assertIn("--admin", str(ctx.exception))
            self.assertNotEqual(state["model"], "")
        finally:
            mock.close()

    def test_koboldcpp_gates_generate_and_streaming(self):
        backend = build("k", ("127.0.0.1", 1), "http://127.0.0.1:2",
                        "koboldcpp")
        for path in ("/api/v1/generate", "/api/extra/generate/stream",
                     "/v1/chat/completions", "/v1/completions"):
            self.assertTrue(backend.gates("POST", path), path)
        self.assertFalse(backend.gates("POST", "/api/extra/perf"))

    def test_lmstudio_gates_chat_and_embedding_paths(self):
        backend = build("l", ("127.0.0.1", 1), "http://127.0.0.1:2", "lmstudio")
        for path in ("/v1/chat/completions", "/api/v0/chat/completions",
                     "/api/v0/embeddings"):
            self.assertTrue(backend.gates("POST", path), path)
        self.assertFalse(backend.gates("POST", "/api/v0/models"))

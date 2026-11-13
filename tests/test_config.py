import os
import unittest

from gpu_lease.config import ConfigError, from_text, load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")


class TestConfig(unittest.TestCase):
    def test_llama_comfyui_example(self):
        cfg = load(os.path.join(EXAMPLES, "llama-comfyui.toml"))
        self.assertEqual(cfg.names(), ["llm", "comfy"])
        self.assertEqual(cfg.lease.mode, "queue")
        self.assertEqual(cfg.lease.grace, 240)

        llm, comfy = cfg.backends
        self.assertEqual(llm.listen, ("127.0.0.1", 6772))
        self.assertEqual(llm.upstream, "http://127.0.0.1:6872")
        self.assertTrue(llm.gates("POST", "/v1/chat/completions"))
        self.assertTrue(llm.gates("POST", "/v1/embeddings"))
        self.assertTrue(llm.gates("POST", "/embedding"))       # llama.cpp native
        self.assertFalse(llm.gates("POST", "/v1/models/unload"))
        self.assertFalse(llm.gates("GET", "/v1/chat/completions"))

        self.assertTrue(comfy.gates("POST", "/prompt"))
        self.assertFalse(comfy.gates("POST", "/upload/image"))
        self.assertEqual(comfy.hold, "watch")
        self.assertEqual(comfy.watch.timeout, 2700)
        self.assertEqual(comfy.watch.id_from, "prompt_id")

    def test_minimal_example(self):
        cfg = load(os.path.join(EXAMPLES, "minimal.toml"))
        self.assertEqual(cfg.lease.mode, "reject")
        self.assertTrue(all(b.kind == "openai" for b in cfg.backends))

    def test_custom_hooks_example(self):
        cfg = load(os.path.join(EXAMPLES, "custom-hooks.toml"))
        sdnext = cfg.by_name("sdnext")
        self.assertEqual(sdnext.evict_hook.kind, "http")
        self.assertEqual(sdnext.evict_hook.path, "/sdapi/v1/unload-checkpoint")
        self.assertEqual(sdnext.watch.done_when, "status_completed")
        self.assertTrue(sdnext.gates("POST", "/sdapi/v1/txt2img"))
        self.assertFalse(sdnext.gates("POST", "/sdapi/v1/options"))

    def test_unknown_key_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            from_text({"backend": [{"name": "a", "listen": 1,
                                    "upstream": "http://x:1", "gate": True}]})

    def test_unknown_lease_key_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            from_text({"lease": {"timesout": 1},
                       "backend": [{"name": "a", "listen": 1,
                                    "upstream": "http://x:1"}]})

    def test_duplicate_listen_address_is_rejected(self):
        with self.assertRaises(ConfigError):
            from_text({"backend": [
                {"name": "a", "listen": "127.0.0.1:7000", "upstream": "http://x:1"},
                {"name": "b", "listen": "127.0.0.1:7000", "upstream": "http://x:2"},
            ]})

    def test_listen_forms(self):
        cfg = from_text({"backend": [
            {"name": "a", "listen": 7001, "upstream": "http://x:1"},
            {"name": "b", "listen": "0.0.0.0:7002", "upstream": "http://x:2"},
            {"name": "c", "listen": "[::1]:7003", "upstream": "http://x:3"},
        ]})
        self.assertEqual([b.listen for b in cfg.backends],
                         [("127.0.0.1", 7001), ("0.0.0.0", 7002), ("::1", 7003)])

    def test_bad_mode(self):
        with self.assertRaises(ConfigError):
            from_text({"lease": {"mode": "priority"},
                       "backend": [{"name": "a", "listen": 1,
                                    "upstream": "http://x:1"}]})

    def test_upstream_must_be_http(self):
        with self.assertRaises(ConfigError):
            from_text({"backend": [{"name": "a", "listen": 1,
                                    "upstream": "127.0.0.1:6872"}]})

    def test_hook_table_overrides_the_preset(self):
        cfg = from_text({"backend": [{
            "name": "llm", "kind": "llama", "listen": 1,
            "upstream": "http://x:1",
            "evict": {"kind": "http", "path": "/drop"},
            "watch": {"timeout": 12},
        }]})
        backend = cfg.backends[0]
        self.assertEqual(backend.evict_hook.kind, "http")
        self.assertEqual(backend.watch.timeout, 12)
        # hold stays "response" for the llama preset
        self.assertEqual(backend.hold, "response")


class TestIdleFree(unittest.TestCase):
    def _cfg(self, extra):
        row = {"name": "comfy", "kind": "comfyui",
               "listen": "127.0.0.1:8188",
               "upstream": "http://127.0.0.1:8198"}
        row.update(extra)
        return from_text({"backend": [row]})

    def test_free_after_is_parsed(self):
        cfg = self._cfg({"idle": {"free_after": 3600}})
        self.assertEqual(cfg.backends[0].idle_free_after, 3600)

    def test_absent_idle_means_off(self):
        self.assertEqual(self._cfg({}).backends[0].idle_free_after, 0)

    def test_unknown_idle_key_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            self._cfg({"idle": {"freeafter": 60}})
        self.assertIn("freeafter", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

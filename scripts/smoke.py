"""Live smoke test: the new package against the real llama.cpp and ComfyUI."""
import json
import os
import subprocess
import sys
import time
import urllib.request

REPO = r"C:\Users\terah\Projects\gpu-lease"
PY = r"C:\Python313\python.exe"
CFG = os.path.join(os.environ["TEMP"], "gpu-lease-smoke.toml")

open(CFG, "w", encoding="utf-8").write("""
[lease]
name = "gpu-lease-smoke"
queue_timeout = 120
grace = 10
early_headers_after = 5
keepalive = 2

[logging]
level = "debug"

[[backend]]
name = "llm"
kind = "llama"
listen = "127.0.0.1:6780"
upstream = "http://127.0.0.1:6772"

[[backend]]
name = "comfy"
kind = "comfyui"
listen = "127.0.0.1:8180"
upstream = "http://127.0.0.1:8188"
""")

env = dict(os.environ, PYTHONPATH=os.path.join(REPO, "src"))


def run(args, **kw):
    return subprocess.run([PY, "-m", "gpu_lease"] + args, cwd=REPO, env=env,
                          capture_output=True, text=True, **kw)


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read()


def post(url, doc, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(doc).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), time.time() - t0


print("== doctor")
print(run(["doctor", "-c", CFG]).stdout)

log = open(os.path.join(os.environ["TEMP"], "gpu-lease-smoke.log"), "w", encoding="utf-8")
proc = subprocess.Popen([PY, "-m", "gpu_lease", "run", "-c", CFG], cwd=REPO,
                        env=env, stdout=log, stderr=subprocess.STDOUT)
try:
    for _ in range(40):
        try:
            get("http://127.0.0.1:6780/__lease/status", timeout=2)
            break
        except OSError:
            time.sleep(0.25)

    print("== status")
    print(get("http://127.0.0.1:6780/__lease/status")[1].decode())

    print("== chat through the gate (llama preset is sleeping: expect a load)")
    code, body, secs = post("http://127.0.0.1:6780/v1/chat/completions", {
        "model": "gemma-4-E2B-it.Q8_0",
        "messages": [{"role": "user", "content": "say hi in three words"}],
        "max_tokens": 24, "stream": False, "temperature": 0})
    doc = json.loads(body)
    print("   %s in %.1fs -> %r" % (code, secs,
          (doc.get("choices") or [{}])[0].get("message", {}).get("content", "")[:60]))

    print("== comfy prompt through the gate (invalid workflow: expect a refusal)")
    try:
        code, body, secs = post("http://127.0.0.1:8180/prompt", {"prompt": {}})
        print("   %s in %.1fs" % (code, secs))
    except urllib.error.HTTPError as exc:
        print("   %s in ComfyUI's own words: %s" % (exc.code, exc.read()[:90]))

    time.sleep(0.5)
    print("== status after")
    print(get("http://127.0.0.1:6780/__lease/status")[1].decode())
finally:
    proc.terminate()
    proc.wait(timeout=20)
    log.close()
    print("== gate log")
    print(open(os.path.join(os.environ["TEMP"], "gpu-lease-smoke.log"),
               encoding="utf-8", errors="replace").read().strip())

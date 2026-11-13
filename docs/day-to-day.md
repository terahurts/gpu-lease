# Day to day

Once it's set up, `gpu-lease` is a window you leave open. This page is about what
you'll see in it and what to do.

---

## The morning routine

Start things in any order. Really — the doorman waits up to four minutes
(`grace`) for a program that hasn't finished booting, so racing them is fine.

1. llama.cpp (as usual)
2. ComfyUI (as usual)
3. the doorman — `gpu-lease run -c gpu-lease.toml`, or the `.bat` from step 11
4. your app(s)

If you'd rather have one click, put all four in one startup file:

```bat
start "" cmd /k C:\llama\start-llama.bat
start "" cmd /k C:\ComfyUI\run_nvidia_gpu.bat
start "" cmd /k C:\gpu-lease\examples\windows\gpu-lease.cmd
start "" "C:\Program Files\MyChatApp\MyChatApp.exe"
```

## Reading the log

The window prints one line per event. `info` level is the useful one:

| line | meaning |
|---|---|
| `gpu-lease: one lease, 2 front door(s)` | started; both doors are open |
| `llm waited 38.4s for the card` | a chat turn queued behind something (an image) and then ran |
| `comfy job 24a1f42a holds the card` | a render started and the doorman is holding the card for it |
| `comfy job 24a1f42a done after 40.7s` | the render finished; the card is free |
| `evicted llm: unloaded gemma-4-E2B-it.Q8_0 (0.8s)` | a model was unloaded to make room |
| `loading Qwen3-Embedding-0.6B ...` / `loaded ... in 27.8s` | a model is loading, and the doorman is waiting for it (correct — this is the barrier) |
| `llm turn done after 12.6s` | a request finished normally |
| `pass-through POST /v1/xxx (no lease)` | **only at `debug` level** — a request that used the card without queuing. Usually harmless (a poll, a listing). Worth a look if it happens during a generation |
| `evicted comfy: not answering /free - continuing` | ComfyUI didn't reply to the "free your memory" request. The turn still proceeds |

Nothing here needs action. The log is for the *"why did that pause?"* question:
there will almost always be a `waited ... for the card` line in it.

## Checking who has the card

From the `gpu-lease` folder:

```powershell
gpu-lease status -c gpu-lease.toml
```

```
gpu-lease  gpu-lease  [queue, queue timeout 1800s]
  holder   comfy (1 turn(s), 38.4s, 1 waiting)
  vram     11,084 / 16,376 MiB
  llm      http://127.0.0.1:6772  ->  http://127.0.0.1:6872  ok
  comfy    http://127.0.0.1:8188  ->  http://127.0.0.1:8198  ok
  llm      8 preset(s), resident: Qwen3-Embedding-0.6B
```

- **holder** — who is using the card right now, for how long, and how many
  requests are waiting behind it.
- **vram** — what the card says, straight from the driver.
- **resident** — which llama.cpp models are actually loaded. This is the line
  that tells you whether a pause was a load or a queue.
- `not running (no answer on …)` + exit code **3** means the doorman isn't up.

## Stopping it

Press **Ctrl+C** in its window, or close the window. The programs keep running
and keep their models; they're just no longer taking turns. Your apps go back to
talking to... nothing, on the old addresses — so stop the apps first, or know
that you'll need to point them at the new ports to keep working without the
doorman.

> **If the doorman is killed mid-turn** (crash, power loss, closing the window
> accidentally): nothing is stuck. The queue belongs to the operating system,
> which cleans up when a program dies. Start it again and it works.

## Pausing the queue for a big job

Sometimes you want the card *entirely* to yourself — a LoRA training run, a
benchmark, a video render. Hold the ticket from the terminal:

```powershell
gpu-lease lock --timeout 7200 -- python train_lora.py
```

Everything waits until that command exits (two hours max here). Other requests
just sit in `waited ... for the card` until then, which is the point.

To keep the card reserved while you click around in other programs instead,
give it something harmless to wait on — a long sleep:

```powershell
gpu-lease lock --timeout 1800 -- python -c "import time; time.sleep(1800)"
```

Press Ctrl+C there when you're done; the queue resumes immediately.

## A checklist for "is it working?"

1. `gpu-lease status` says `holder nobody (GPU free)` when idle. ✅
2. Send a chat message — the log says `llm turn done after Ns`, and N is small
   once the model is warm. ✅
3. Make an image — the log says `comfy job … holds the card` then `done after`. ✅
4. Start an image, then immediately send a chat — the chat pauses, then answers,
   and the log says `llm waited Ns for the card`. ✅

If all four are true, the whole thing is doing its job and you can stop thinking
about it.

## Updating

There's nothing to update automatically. If you replace the folder with a newer
copy, run `python -m pip install -e .` again from inside it. Your
`gpu-lease.toml` is yours — copy it into the new folder.

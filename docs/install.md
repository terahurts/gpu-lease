# Install, step by step

This guide assumes you are not a programmer. Every click and keystroke is
written out. If a step says **You should see**, stop and compare — that's how
you know you're still on track.

> **Nothing bad happens if you stop halfway.** Steps 1–5 change nothing. The
> switch happens in step 6, and [troubleshooting.md](troubleshooting.md#how-to-undo-everything)
> undoes it.

---

## Step 1 — Where the folder is

The program lives in a folder on this computer:

```
C:\Users\terah\Projects\gpu-lease
```

Open it with File Explorer to have a look. You will see `README.md`,
`src`, `docs`, `examples`. That's the whole thing; there is nothing to
download.

> If you are setting this up on a *different* computer, copy the whole
> `gpu-lease` folder across. It has no installer and no other files elsewhere.

## Step 2 — Open a terminal

A terminal is a window where you type commands instead of clicking. You will
use it three times in this guide.

1. Press the **Windows key**.
2. Type `terminal`.
3. Press **Enter** on **Windows Terminal** (or **PowerShell** on older
   machines).

**You should see** a window with a blinking cursor and a line ending in `>`.

## Step 3 — Install

Type this (you can paste it with a right-click) and press Enter:

```powershell
cd C:\Users\terah\Projects\gpu-lease
python -m pip install -e .
```

**You should see** a few lines ending in something like:

```
Successfully installed gpu-lease-0.1.0
```

Then check it worked:

```powershell
gpu-lease --version
```

**You should see** exactly:

```
gpu-lease 0.1.0
```

> **If you see `python : The term 'python' is not recognized`** — Python isn't
> installed, or isn't on your PATH. Install Python from
> [python.org/downloads](https://www.python.org/downloads/) and **tick "Add
> python.exe to PATH"** during setup, then close and reopen the terminal.
>
> **If you see `gpu-lease : not recognized`** — the install didn't finish. The
> last lines of the previous command will say why (usually a typo in the folder
> path). You can always run it without installing:
> `python -m gpu_lease --version` from inside the folder.

## Step 4 — Find the two addresses

You need to know the two addresses your apps already use. They look like
`http://127.0.0.1:6772/v1`.

Where to find them:

- **In your chat app / image app**: the "Connections" or "Settings" screen. Copy
  the two URLs somewhere — you will paste them back in step 7.
- **In the windows where the two programs run**: llama-server prints
  `listening on http://127.0.0.1:6772`; ComfyUI prints
  `Starting server ... To see the GUI go to: http://127.0.0.1:8188`.

Write them down like this:

```
chat   http://127.0.0.1:______ /v1
images http://127.0.0.1:______
```

The numbers in those blanks are **ports** — see [glossary.md](glossary.md).

> If both programs are on the same port (very unusual), stop and ask for help;
> the switch in step 6 needs two different numbers.

## Step 5 — Write the settings file

The program needs a small text file telling it the two addresses. You don't have
to write it from scratch.

In the same terminal:

```powershell
gpu-lease init -o gpu-lease.toml
```

**You should see** `wrote C:\Users\terah\Projects\gpu-lease\gpu-lease.toml`.

Now open it in Notepad:

```powershell
notepad gpu-lease.toml
```

You will see sections in square brackets like `[lease]`, and lines with `=`
signs. **Only the four lines marked with `# ← yours` at the end need your
numbers** — and only if your ports aren't 6772 and 8188:

```toml
[[backend]]
name = "llm"
kind = "llama"
listen = "127.0.0.1:6772"           # ← this is the address your CHAT app uses
upstream = "http://127.0.0.1:6872"  # ← this is where llama.cpp will move TO

[[backend]]
name = "comfy"
kind = "comfyui"
listen = "127.0.0.1:8188"           # ← the address your IMAGE app uses
upstream = "http://127.0.0.1:8198"  # ← where ComfyUI will move TO
```

Rules of thumb, so you can't get it wrong:

- **`listen`** = the address your apps use *today*. It does not change.
- **`upstream`** = the address the real program will move *to* in step 6. Use
  **your port + 100** (6772 → 6872, 8188 → 8198). Any free number works.
- Keep the quotes. Keep the `http://`. Don't touch anything else yet.

Save with **Ctrl+S**, close Notepad.

The other lines (how long to wait, priorities) are explained in
[config-explained.md](config-explained.md) — the defaults are already tuned for
a chat-plus-images setup, so you can leave them.

## Step 6 — Move the two programs one port up

Right now each program owns the address your apps use. The doorman needs to own
that address instead, so each program moves aside. **This is the only change
made to how your programs start.**

**llama.cpp** — find the line that starts `llama-server.exe` (a `.bat` file, a
shortcut, or a terminal window you open by hand). Find `--port 6772` and change
the number to the `upstream` number from step 5:

```
--port 6872
```

**ComfyUI** — open the `.bat` file you normally double-click (in a portable
install it's usually `run_nvidia_gpu.bat`). It probably ends with a line like:

```
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build
```

Add the port to the end of that line, on the same line:

```
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --port 8198
```

> **ComfyUI ignores extra arguments if you pass them to the `.bat` from
> outside.** You have to edit the file itself. Right-click it → **Edit**.

Save both files.

## Step 7 — Point your apps at the doorman

This step usually changes nothing — because the doorman took the addresses your
apps already had. Check anyway:

1. Open your app's connection settings.
2. The chat address should still be `http://127.0.0.1:6772/v1`.
3. The image address should still be `http://127.0.0.1:8188`.
4. If either has an **extra** address field (some apps have a separate
   "embedding endpoint" or "custom endpoint" box), **empty it**. An override in
   that box is the one way to accidentally skip the doorman.

Use the app's own **Test** / **Test connection** button if it has one.

> **Why not just point the apps at new numbers?** You can, but every app, script
> and browser tab that ever knew the old numbers would then bypass the queue.
> Keeping the familiar numbers on the doorman is what makes it airtight.

## Step 8 — Start the two programs

Start llama.cpp and ComfyUI the way you normally do (with the edited files).
Wait for ComfyUI to finish booting — it prints
`To see the GUI go to: http://127.0.0.1:8198` and is ready a few seconds after.

**You should see** the new port numbers in both windows (`6872`, `8198`).
Confirm with:

```powershell
curl http://127.0.0.1:6872/v1/models
curl http://127.0.0.1:8198/queue
```

Both should answer with a wall of text, not an error.

## Step 9 — The health check

In the terminal, from the `gpu-lease` folder:

```powershell
gpu-lease doctor -c gpu-lease.toml
```

**You should see** something like:

```
config      C:\Users\terah\Projects\gpu-lease\gpu-lease.toml
lock        'gpu-lease' (mode queue)
vram        2,206 / 16,376 MiB used
gate        not running
  llm     llama  listen 127.0.0.1:6772   free      upstream answers 200
          gated: POST /.../completions, /.../embeddings, ...
  comfy   comfyui listen 127.0.0.1:8188   free      upstream answers 200
no problems found
```

`free` means the doorman's address is available; `upstream answers 200` means
the real program is alive behind it. **`no problems found` is the line you
want.** If you get `problem(s)` instead, each one is written in plain words —
see [troubleshooting.md](troubleshooting.md), or run `gpu-lease doctor` again
after fixing it.

## Step 10 — Start the doorman

```powershell
gpu-lease run -c gpu-lease.toml
```

**You should see:**

```
[12:04:11] gpu-lease: one lease, 2 front door(s)
[12:04:11]   llm    http://127.0.0.1:6772  ->  http://127.0.0.1:6872
[12:04:11]   comfy  http://127.0.0.1:8188  ->  http://127.0.0.1:8198
[12:04:11]   lock 'gpu-lease', mode queue, queue timeout 1800s
[12:04:11]   ctrl-c to stop
```

Leave this window open. It **is** the doorman; closing it stops the queue
(your programs keep running, they just stop being managed).

Now test for real:

1. **In your chat app, send a message.** It should answer. The first message
   after starting may take 10–40 seconds — that's a model loading, and the
   doorman waits for it on purpose.
2. **Make an image.** The doorman's window prints `comfy … holds the card`, then
   `… done after 40s`.
3. **The real test:** make an image, and while it renders, send a chat message.
   The chat waits for the render to finish, then answers normally. That's the
   whole feature working.

## Step 11 — Make it start every morning

Typing that command daily gets old. One `.bat` file fixes it.

1. In File Explorer, go to `C:\Users\terah\Projects\gpu-lease\examples\windows`.
2. Right-click **gpu-lease.cmd** → **Edit**.
3. Change the `REPO=` line if your folder is somewhere else, and the `PYTHON=`
   line if `py -3.13` isn't what you use (`python` is a safe substitute).
4. Save, then **copy the file** to wherever your other launch files live.
5. Add one line to your startup batch, next to the llama.cpp and ComfyUI lines:

```
start "" cmd /k C:\path\to\gpu-lease.cmd
```

Startup order doesn't matter — the doorman waits up to four minutes for a
program that hasn't finished booting yet.

---

## You're done

Normal life from here is [day-to-day.md](day-to-day.md). If something looks
wrong, [troubleshooting.md](troubleshooting.md) is organised by *symptom*, in
the same words you'd use to describe it out loud.

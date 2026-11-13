# Start here

**What this is, in one paragraph.** You have one graphics card and two programs
that both want all of it — a chat model and an image generator. When they both
load at the same moment, the card runs out of memory and something breaks.
`gpu-lease` is a small program that sits between your apps and those two
programs and makes them take turns. That's the whole idea.

**What it is not.** It doesn't change your models, your apps, or your settings
inside them. It doesn't run in the cloud. It isn't a driver or a service that
starts itself behind your back. If you stop it, you are back where you started.

---

## The picture

```
           before                                 after
  ┌──────────────┐                      ┌──────────────┐
  │  your chat   │──► llama.cpp ──┐     │  your chat   │──► gpu-lease ──► llama.cpp
  │  your images │──► ComfyUI ────┤     │  your images │──►           ──► ComfyUI
  └──────────────┘    both load   │     └──────────────┘        one at a time
                      at once ✗   ▼
                          out of memory
```

Your apps keep talking to the same addresses. `gpu-lease` answers on those
addresses, and quietly moves the real programs one step aside. That way nothing
in your apps has to be reconfigured — and nothing can accidentally sneak past
the queue.

## Think of it as a doorman

- Each app asks the doorman for the room (the graphics card).
- If the room is free, the app goes in.
- If someone is already inside, the app waits in a queue, in arrival order.
- Before letting a new guest in, the doorman asks the previous one to leave and
  takes its coat back (frees the memory).
- When the guest is done, the doorman lets the next one in.

The doorman is very fast at throwing people out — usually about one second — so
turns feel normal. The slow part is loading a model, and that only happens when
a model isn't already loaded.

## What you need before you start

| you need | how to know you have it |
|---|---|
| Windows 10 or 11 | you're reading this on it |
| llama.cpp **or** ComfyUI already working | you can chat / make an image today |
| Both pointed at by your app today | your app has a "connection" with a URL in it |
| 15 quiet minutes | the first setup is the fiddly part |

You do **not** need: programming experience, an editor other than Notepad,
a GitHub account, or an internet connection while it runs.

## The five steps

1. **Install it.** One command in a terminal. → [install.md](install.md#step-3--install)
2. **Write a small settings file** — three addresses and a handful of numbers,
   all explained line by line. → [install.md](install.md#step-5--write-the-settings-file)
3. **Move your two programs one port aside** (one extra word in each launch
   command). → [install.md](install.md#step-6--move-the-two-programs-one-port-up)
4. **Start it and check** with a built-in "is everything all right?" command.
   → [install.md](install.md#step-9--the-health-check)
5. **Use your apps as usual.** Nothing looks different, except that turns now
   wait for each other instead of colliding.

## After that

| when you want to | read |
|---|---|
| understand a setting, or change how long things wait | [config-explained.md](config-explained.md) |
| start everything with one click every morning | [day-to-day.md](day-to-day.md) |
| know what the log lines mean | [day-to-day.md](day-to-day.md#reading-the-log) |
| fix something that looks broken | [troubleshooting.md](troubleshooting.md) |
| go back to the old way, today | [troubleshooting.md](troubleshooting.md#how-to-undo-everything) |
| know whether it's safe, slow, or nosy | [faq.md](faq.md) |
| a word in these docs didn't make sense | [glossary.md](glossary.md) |

## If you only read one thing

**It is entirely reversible.** Nothing is installed into your apps, nothing is
uninstalled from them, and no file outside this folder is touched. To undo:
stop the doorman, change two URLs back in your app, and put the two programs
back on their old ports. Five minutes, and the computer is exactly as it was.

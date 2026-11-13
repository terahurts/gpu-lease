# Questions people actually ask

**Is it safe? Can it break my models or my app?**

It never touches your models, your app's database, or anything outside its own
folder. It only sends three kinds of request to your two programs, all of which
those programs already support: "unload this model", "load this model", and "free
your memory". Your app still sends exactly the requests it always did; the
doorman just waits its turn before passing them along.

**Does it make things slower?**

When only one program is running: no. The overhead is milliseconds — it's a
pass-through.

When both want the card: something has to wait, and that's the point. The card
can't be in two places. What you're buying is that waiting is *orderly* instead
of an out-of-memory error.

The one real cost: switching from a chat model to an image model means the chat
model gets unloaded. Switching back means loading it again (6–40 s depending on
size). The doorman keeps whichever side is in use loaded, so this only happens
on real mode switches, not every turn.

**What if it crashes while an image is rendering?**

Nothing is lost. The render keeps going in ComfyUI's own process; the doorman
was only watching. Restart the doorman and the queue works again immediately —
the "who has the card" ticket belongs to the operating system, which cleans up
after a program that dies. You'll never find the card locked until tomorrow.

**Does it send anything over the internet?**

No. It listens on `127.0.0.1` by default, which means only this computer can
reach it. It talks only to the two addresses in your settings file. There is no
telemetry, no update check, no account.

**Can I use it with only one of the two programs?**

Yes. Delete the `[[backend]]` block you don't need and keep the other. You lose
nothing — with one program there's no queue to arbitrate, but the health check
and status still work.

**Can I use it with a program you've never heard of?**

Yes, if it has an HTTP API. Use `kind = "generic"` and tell it which paths need
the card, plus (optionally) how to make the program free its memory. See
[config-explained.md](config-explained.md#the-three-hooks-if-your-program-isnt-llamacpp-or-comfyui).
The free-memory part is optional; without it, the doorman still queues, it just
can't force the other program to let go.

**Does it work with Ollama, LM Studio or KoboldCpp?**

It *should*. Each has a ready-made `kind` (`"ollama"`, `"lmstudio"`,
`"koboldcpp"`) that queues its requests and knows how to make it let go of the
card. But only llama.cpp and ComfyUI have been tested end to end; the other
three were written from their documentation. Treat them as "should work", not
"does work". YMMV.

**I have two graphics cards. Does it use both?**

No. One lease = one card's worth of turns. Two cards means two copies of the
doorman, each with its own settings file, ports and `name`, with each set of
programs assigned to one card (most programs have a "which GPU" option). It is
not a cluster scheduler and doesn't pretend to be.

**Why not just use a smaller model / fewer workers?**

That's often the right answer, and cheaper. `gpu-lease` is for when you want
both programs at full size and they genuinely don't fit at the same time. If
shaving a model down makes them fit, do that first.

**Why not run llama.cpp on the CPU or the integrated graphics?**

Also often the right answer for a small embedding model. That's a *different*
fix: it removes the contention instead of scheduling it. The short version: if
it fits outside the card, keep it outside the card — a 0.5 GB embedding model on
the CPU costs a few milliseconds per recall and frees 5 GB on the GPU. Use
`gpu-lease` for the pair that genuinely can't leave the card.

**Does it work on Linux or a Mac?**

The queue itself does (Linux is supported today; a Mac would need testing). The
llama.cpp and ComfyUI knowledge is identical; only the "who owns the ticket"
mechanism differs underneath. The docs you're reading are Windows-specific
because that's the common case for this exact pair.

**Can other computers on my network use it?**

Yes — start it with `--host 0.0.0.0` and set a `token` per backend, then give the
other machine the address plus the token. Without a token, anything on your
network could use your models. Don't do that on a network you don't trust.

**Can my app and a script use it at the same time?**

Yes. They share one queue, which is usually what you want: your script won't
start a render while your app is mid-reply. If a script needs the card *only*
for itself, `gpu-lease lock -- yourcommand` does that.

**How do I know it's actually doing anything?**

`gpu-lease status` while both are busy: it names the holder and the number of
waiters. And the four-step check at the end of
[day-to-day.md](day-to-day.md#a-checklist-for-is-it-working) takes a minute.

**Will this still work if I change my models / workflows / app?**

Yes. It doesn't know anything about your models, workflows or app, only about
addresses and two programs' standard "unload/load/free" requests. Changing a
preset name in llama.cpp is fine — the doorman reads the name from your app's
request.

**Do I have to leave the window open?**

Yes. The window *is* the program. If you close it, the queue stops (your two
programs keep running). Make it start with everything else — step 11 of
[install.md](install.md#step-11--make-it-start-every-morning).

**Can I run it as a Windows service, in the background, with no window?**

It's a normal program; it needs a console. The simplest invisible option is a
Task Scheduler task set to "run whether user is logged on or not" — but then its
log has no window, which makes the "why did that pause?" question harder. A
minimised terminal window is the sane middle ground.

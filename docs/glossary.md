# Words used in these docs

Short, practical definitions. You don't need to memorise any of this — it's here
for when a sentence doesn't land.

**API**
The way one program asks another program to do something, over the network. When
your app "talks to" llama.cpp, it's sending API requests.

**Backend** (in this project)
One of the programs being managed — one `[[backend]]` block in the settings
file. Not the same as "backend" in a web app; here it just means "one program
behind one door".

**ComfyUI**
The image/video generator. It has a web page and an API on the same port.

**Config file / settings file**
A plain text file (here: `gpu-lease.toml`) that tells a program how to behave.
Open it in Notepad, change a value, save. The `#` character starts a note that
the program ignores.

**Endpoint**
A specific address for one kind of request — `/v1/chat/completions` is the
"chat" endpoint, `/v1/embeddings` is the "turn text into numbers" endpoint.

**Evict**
Make a program give its graphics memory back. For llama.cpp: unload the model.
For ComfyUI: free cached models and memory.

**Gate / doorman**
`gpu-lease` itself, when it's running. It answers on the addresses your apps
already know and decides whose turn it is.

**Hold (a turn)**
How long the doorman keeps the card reserved for one request. Usually until the
last byte of the reply; for image renders, until the render actually finishes.

**llama.cpp / llama-server**
The chat-model program. `llama-server` is its executable; "router mode" means one
`llama-server` can serve several presets (different models) and switch between
them.

**Lease**
The "ticket" for the card. Whoever holds the lease may use the GPU; everyone
else waits. Exactly one lease exists per settings file.

**Port**
A numbered door on your computer. `127.0.0.1:8188` means "this computer, door
8188". Two programs can't use the same door at the same time — which is why
moving a program one door aside is how the doorman gets to answer on the
original.

**Preset** (llama.cpp)
A named model configuration in `config.ini` — for example
`gemma-4-E2B-it.Q8_0`. Your app names the preset it wants in each request.

**Queue mode / reject mode**
What happens when the card is busy. `queue`: wait in line (`mode = "queue"`).
`reject`: refuse immediately with "busy, try again" (`mode = "reject"`).

**Request / turn**
One thing your app asks for: one chat reply, one image. The doorman schedules
*turns*, not whole sessions.

**Resident**
A model that is currently loaded on the card. `gpu-lease status` lists them.

**Sleeping** (llama.cpp)
A model that has been unloaded from memory by `--sleep-idle-seconds` but still
occupies llama.cpp's single model slot. It looks unloaded but must be unloaded
properly before a different preset can load.

**Terminal / PowerShell / cmd**
The text window where you type commands. `cmd` is the old one, PowerShell the
newer one, Windows Terminal the window that hosts either. All three can run the
commands in these docs.

**TOML**
The format of the settings file. Lines like `key = value`, sections in square
brackets. Names and text need quotes; plain numbers don't.

**Upstream**
The real program's own address, one port aside from the door the doorman
answers on. `listen = "…:8188"` (your app) → `upstream = "http://…:8198"` (the
program).

**VRAM**
Video RAM: the memory on the graphics card. The thing that runs out. Measured
in MiB (1 MiB ≈ a million characters of text, or one smallish image).

**WDDM**
Windows' graphics driver model. When VRAM runs out, it can quietly move data into
ordinary system RAM instead of erroring — the program keeps "working", just
several times slower, with no error message anywhere. This silent failure is one
of the reasons a doorman is worth having.

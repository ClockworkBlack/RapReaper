RAPREAPER'S LOCAL AI
====================

An open AI model that runs on your own computer and drives RapReaper: no account, no subscription, and nothing you
say to it leaves the computer. It is the default way to use RapReaper when you have no other AI app; RapReaper works
just as well with Claude, ChatGPT's desktop app, Cursor, VS Code, LM Studio and any other app that speaks MCP.

What it is made of (all downloaded once by "Get Local AI", from their own authors, each checked by its SHA-256):

  llama.cpp, build b12250       llama-server: runs the model, shows a chat window in your browser, and starts
  (github.com/ggml-org/          RapReaper's tools for it (MCP). MIT licence (licenses/llama.cpp - LICENSE.txt).
  llama.cpp, 10 November 2026)  Windows: the Vulkan build (uses most graphics cards, else the processor).
                                Mac: the Apple-chip build (uses the graphics part of the chip) or the Intel build.

  Qwen 3.5, by the Qwen team    The AI model, made for working with tools. Apache 2.0 licence (licenses/Qwen 3.5 -
  (GGUF files by unsloth, on    NOTICE.txt). Two sizes:
  huggingface.co)                 4B  2.7 GB  for computers with about 8 GB of memory (the default on most)
                                  9B  5.7 GB  for 16 GB or more; better answers, quick only with a good graphics
                                              card or an Apple chip (the default on an Apple-chip Mac with 16 GB)

How to use it

  1. Install RapReaper first (Install RapReaper.bat / .command).
  2. Double-click "Get Local AI" (.bat on Windows, .command on a Mac) once. It asks which model when you have the
     memory for the larger one, downloads llama.cpp and the model, checks them, and says when it is done.
  3. Double-click "Start Local AI" whenever you want to use it. Its window shows what it is doing; your browser opens
     the chat window (an address like http://127.0.0.1:8077, which only this computer can reach). Keep the window open
     while you work; close it to stop the Local AI.
  4. Start REAPER with your project, and type in the chat:  Run the RapReaper self-test

What to expect

  - It is slower and less careful than the big online AI models (Claude, ChatGPT). Without a good graphics card, and on
    an Intel Mac, the first answer of a chat can take a few minutes, because the model first reads RapReaper's tool
    descriptions; later answers come faster. Running on the processor, the 4B model takes about 4 GB of memory, the 9B
    about 7 GB: close other programs on a computer with 8 GB.
  - The chat window asks before every RapReaper tool runs (Allow, Always allow, or Deny). Say yes only to what you
    asked for. RapReaper's own rules still hold: a plan before any cut, every change one step in REAPER's undo history,
    nothing saved unless you ask.
  - It gets RapReaper's 28 core tools (vocal chain, balance, loudness, export, cuts and restore points, effects, tempo
    and key, chains, saving) and a short working method, so that the tool descriptions fit in a small model's memory.
    The rest (separating songs, learning a song's effects, remastering, projects ...) needs a bigger AI app.
  - "Start Local AI" starts llama.cpp only when every one of its files is exactly the one Get Local AI checked
    (runtime-b12250.json lists them) and the model has its published checksum. Any other file in LocalAI/runtime
    stops it: llama.cpp loads the libraries next to it, so nothing else may lie there. (The .DS_Store, Thumbs.db and
    desktop.ini files that Finder and Explorer leave in an opened folder are fine.)

If it does not start

  - "not downloaded yet" or "not the ones Get Local AI checked": double-click Get Local AI again.
  - The chat window shows no RapReaper tools: llama.cpp waits only 10 seconds for them while it starts. Start Local AI
    checks them first and starts llama.cpp once more by itself when needed; if it still says so, close its window,
    close other busy programs, and start it again.
  - It stops while starting on the graphics card: Start Local AI tries once more on the processor by itself. To use the
    processor only from the start:  py rapreaper_install.py --local-ai --cpu   (python3 on a Mac).
  - Slow answers: close other programs; the 4B model is quicker than the 9B. Thinking before answering is off for speed;
    --think switches it on (better choices, slower).
  - Everything llama.cpp printed is in kit/state/local_ai/llama-server.log.

The model and llama.cpp stay in LocalAI/models and LocalAI/runtime when you update RapReaper. To remove the Local AI,
delete the files in those two folders.

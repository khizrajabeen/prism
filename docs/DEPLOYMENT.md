# Local deployment

Getting NeoBrain running on your own machine, keeping it running, and choosing
where the reasoning happens.

---

## 1. Install

```bash
git clone <repo> ~/neobrain && cd ~/neobrain
python -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -e .                    # core only: requests + PyYAML
neobrain init
neobrain doctor
```

Optional extras, add them when you need them:

```bash
pip install -e ".[embeddings]"   # local vectors for hybrid retrieval
pip install -e ".[pdf]"          # ingest paywalled PDFs you downloaded yourself
pip install -e ".[mcp]"          # MCP server so agent clients get the tools
pip install -e ".[all]"
```

`NEOBRAIN_HOME` controls where state lives. Set it if you want the code in one
place and the data in another (e.g. code in `~/src/neobrain`, data in a synced
folder):

```bash
export NEOBRAIN_HOME=~/Research/neobrain
```

## 2. First sweep

```bash
neobrain sweep --days 30      # a few minutes; be patient, it rate-limits itself
neobrain digest
neobrain status
```

If a source fails, the sweep continues and records the error. Check
`neobrain doctor` — it reports whether the last sweep had errors, which is how
you catch a quietly half-broken nightly job.

## 3. Schedule it

### Linux / macOS — cron

```bash
crontab -e
```

```cron
# NeoBrain nightly sweep, 06:00
0 6 * * * cd $HOME/neobrain && $HOME/neobrain/.venv/bin/neobrain sweep >> $HOME/neobrain/logs/sweep.log 2>&1
```

Cron has a minimal environment. Use absolute paths, and if you set
`NEOBRAIN_HOME`, set it in the crontab too.

### Linux — systemd timer (survives reboots more gracefully)

`scripts/systemd/neobrain-sweep.service`:

```ini
[Unit]
Description=NeoBrain literature sweep
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=%h/neobrain
Environment=NEOBRAIN_HOME=%h/neobrain
ExecStart=%h/neobrain/.venv/bin/neobrain sweep
```

`scripts/systemd/neobrain-sweep.timer`:

```ini
[Unit]
Description=Run NeoBrain sweep daily

[Timer]
OnCalendar=*-*-* 06:00:00
Persistent=true          # catches up if the laptop was asleep at 06:00

[Install]
WantedBy=timers.target
```

```bash
mkdir -p ~/.config/systemd/user
cp scripts/systemd/neobrain-sweep.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now neobrain-sweep.timer
systemctl --user list-timers neobrain-sweep.timer
```

`Persistent=true` matters for a laptop — cron silently skips a job if the
machine was asleep; a persistent timer runs it on wake.

### macOS — launchd

```bash
cp scripts/launchd/com.neobrain.sweep.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.neobrain.sweep.plist
```

### Windows — Task Scheduler

```powershell
$action  = New-ScheduledTaskAction -Execute "$HOME\neobrain\.venv\Scripts\neobrain.exe" `
                                   -Argument "sweep" -WorkingDirectory "$HOME\neobrain"
$trigger = New-ScheduledTaskTrigger -Daily -At 6am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
Register-ScheduledTask -TaskName "NeoBrain sweep" -Action $action -Trigger $trigger -Settings $settings
```

`-StartWhenAvailable` is the equivalent of `Persistent=true`.

## 4. Connect your agent

### Claude Code

```bash
claude mcp add neobrain -- neobrain mcp
```

Add the operating contract to your project instructions:

```bash
cat AGENT.md >> CLAUDE.md      # or reference it: "Follow AGENT.md."
```

### Claude Desktop

`claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "neobrain": {
      "command": "/absolute/path/to/neobrain/.venv/bin/neobrain",
      "args": ["mcp"],
      "env": { "NEOBRAIN_HOME": "/absolute/path/to/neobrain" }
    }
  }
}
```

Use the absolute path to the venv binary. Desktop apps do not inherit your
shell's PATH, and this is the single most common reason an MCP server "does not
appear".

### Your own loop

Everything is importable:

```python
from neobrain import config, db, memory, retrieve

cfg = config.load()
con = db.connect()

system_prompt = open("AGENT.md").read() + "\n\n" + memory.brief(con, cfg)
evidence = retrieve.context_pack("what predicts class II immunogenicity?", con=con)
# → send system_prompt + evidence + the user's question to your model
```

## 5. Where the reasoning happens

The data layer is local no matter what. The model is the real decision.

| | Hybrid (local data + API model) | Fully local |
|---|---|---|
| Your PDFs, notes, database | stay on disk | stay on disk |
| What leaves the machine | the current question + retrieved snippets | nothing |
| Quality on hard immunology reasoning | best available | noticeably behind |
| Quality on long agentic tool loops | reliable | degrades with chain length |
| Cost | per token | electricity |
| Works offline | no | yes |

**The practical split most people land on:** a local model for the
high-volume cheap work — triaging 400 abstracts a night, tagging, dedup — and a
frontier model for synthesis, code, and anything going into a thesis. That
keeps cost down without paying for it in the answers you rely on.

If your data is controlled-access (dbGaP, EGA) or identifiable patient data,
**check the DUA before any of it goes near an API model**, and consider that an
agent which indexes it into `brain.db` has created a copy the agreement may not
permit. This is a real compliance question, not a hypothetical one.

### Running a local model

```bash
# Ollama, for both chat and embeddings
ollama pull qwen3:32b            # or whatever fits your VRAM
ollama pull nomic-embed-text
```

```yaml
# config/settings.yaml
embeddings:
  backend: ollama
  model: nomic-embed-text
  ollama_url: http://localhost:11434
```

Rough VRAM guidance: a 7–8B model at 4-bit fits in ~6 GB and is usable for
triage; a 30B+ model needs 24 GB+ and is where local reasoning starts being
worth trusting for real work. CPU-only inference works and is slow enough that
you will stop using it.

## 6. Backup

`brain.db` is one file. Back it up by copying it (safely, while it may be in
use):

```bash
sqlite3 ~/neobrain/brain.db ".backup '/backup/brain-$(date +%F).db'"
```

Version-control the rest — `config/`, `memory/`, `knowledge/` are all text, and
`git log` on `knowledge/` is the record of how your understanding changed.

```bash
cd ~/neobrain && git init && git add -A && git commit -m "initial brain"
```

## 7. Maintenance

```bash
neobrain doctor                  # weekly: is anything quietly broken?
neobrain status                  # corpus growth
neobrain review                  # daily: approve or reject memory edits
sqlite3 brain.db "VACUUM;"       # occasionally, after large deletions
```

If retrieval quality drops as the corpus grows, that is the signal to turn on
embeddings — not before.


---

## Making it faster

Measure before you optimise. `neobrain` ships a profiler harness in the tests,
and the numbers below are from doing exactly that on this codebase — the
result was not what I would have guessed.

| Operation | Before | After | What it actually was |
|---|---|---|---|
| `retrieve.search` | 22.7 ms | 4.0 ms | MMR recomputing every pairwise similarity each iteration |
| `retrieve.multi_search` | 10.9 ms | 1.7 ms | `interests.yaml` re-parsed five times per question |
| `memory.brief` | 9.2 ms | 0.5 ms | same YAML re-parse |
| `db.connect()` | 1.1 ms | 0.6 ms | ~200 DDL statements run on every connection to discover nothing had changed |

**88% of a decomposed search was YAML parsing.** Not retrieval, not SQLite, not
the graph. That is the argument for profiling rather than reasoning about
performance: the obvious suspects were all innocent.

The three fixes, in case your corpus grows into different bottlenecks:

1. **Cache config on mtime** (`config.load`). Invalidate with `config.invalidate()`.
2. **Skip the migration when the schema version already matches** (`db._migrate`).
3. **Incremental MMR** — keep a running "closest already-selected" score per
   candidate instead of recomputing the whole matrix (`retrieve._mmr`).
4. **Cache the entity adjacency list** keyed on edge count (`graph._adjacency`).

### When the corpus gets big

- **Under ~50k chunks**: everything above is fine as-is.
- **50k–500k chunks**: turn on embeddings; the brute-force vector scan is O(n)
  but stays under ~100 ms. Consider `sqlite3` `ANALYZE` after big ingests.
- **Beyond that**: replace the linear scan in `embeddings.search_vectors` with
  an ANN index (hnswlib, or LanceDB alongside `brain.db`). Nothing else has to
  change — that function is the whole seam.
- **Sweep time** is dominated by polite rate limiting, not compute. It is
  deliberately slow; do not parallelise it into a ban.

```bash
sqlite3 brain.db "PRAGMA optimize; VACUUM; ANALYZE;"   # after a large ingest
```

## Voice

The dashboard's voice console uses the **Web Speech API** — no dependency, no
key, no build step. It works in Chrome and Edge; Firefox and Safari do not
implement recognition and there is no polyfill worth having.

**The privacy tradeoff, stated plainly:** in Chrome and Edge, speech
*recognition* streams audio to a cloud service. That is fine for "what's next"
and wrong for dictating unpublished results. Speech *synthesis* (reading
replies back) is on-device and always private.

For fully local speech, run Whisper and point the browser at it instead:

```bash
pip install faster-whisper
# or, faster on CPU:
#   git clone https://github.com/ggerganov/whisper.cpp && make
```

Then record with `MediaRecorder`, POST the blob to a small local endpoint, and
transcribe there. The command grammar in `app.html` (`VOICE_COMMANDS`) is the
only part that needs to change — it takes a string and does not care where the
string came from.

The grammar is deliberately small and fixed rather than free-form intent
guessing. A voice interface that mishears "exclude" as "include" and silently
acts on it is worse than no voice interface.

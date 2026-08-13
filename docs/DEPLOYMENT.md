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

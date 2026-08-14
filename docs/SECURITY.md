# Sandboxing an agent that uses your machine

You want the assistant to operate your laptop. That is a reasonable thing to
want and a bad thing to grant all at once. This is the order that works.

The principle: **widen permissions as the agent earns them, and make every
widening reversible.** Not because the model is malicious, but because it will
occasionally be confidently wrong in a way that touches a filesystem.

---

## Stage 1 — filesystem, scoped

Give it `~/neobrain/` and nothing else. Inside that:

| Path | Access | Why |
|---|---|---|
| `knowledge/`, `memory/` | **read only** | Writes go through proposals, always |
| `config/` | read | It can suggest interest-profile changes; you make them |
| `digests/`, `brain.db` | read (write via tools) | The tools enforce the schema |
| `workspace/` | **read/write** | Scratch space, analysis output, generated code |
| `inbox/` | read | You put PDFs here |

Not your home directory. Not `Documents`. Not the folder with your thesis in
it — a research assistant does not need write access to the thing it is helping
you write, and separating them means an accident is recoverable.

In Claude Desktop, the filesystem MCP server takes explicit allowed directories.
In Claude Code, `--add-dir` and the permission settings in `.claude/settings.json`
do the same job.

## Stage 2 — named tools before a raw shell

The MCP server in `neobrain/mcp_server.py` exposes ~20 typed tools that cover
the actual work: retrieve evidence, read a paper's methods, run a sweep, propose
a memory edit, add a review card.

A raw `bash -c` is a much larger blast radius, and you do not yet know this
agent's failure modes on your machine. Add shell access when you have a
concrete task the named tools cannot do — running your own pipeline, usually —
and then scope it:

```jsonc
// .claude/settings.json
{
  "permissions": {
    "allow": [
      "Bash(neobrain:*)",
      "Bash(git status)", "Bash(git diff:*)", "Bash(git log:*)",
      "Bash(python scripts/*.py:*)"
    ],
    "deny": [
      "Bash(rm:*)", "Bash(curl:*)", "Bash(sudo:*)",
      "Read(./secrets/**)", "Read(~/.ssh/**)", "Read(~/.aws/**)"
    ]
  }
}
```

## Stage 3 — approval on writes and deletes, automatic on reads

Reading is cheap and recoverable. Writing is not. The asymmetry should be in
your permission config, and it is already in the memory design: the agent
proposes, you approve.

**Do not turn off the memory approval gate.** It is thirty seconds a day and it
is the only thing standing between you and an assistant that has quietly
rewritten its own beliefs over three months. There is deliberately no MCP tool
to apply a proposal — `neobrain review` in a terminal is the only path.

## Stage 4 — network allowlist

The sweep needs exactly these hosts:

```
www.ebi.ac.uk          Europe PMC
europepmc.org
eutils.ncbi.nlm.nih.gov  PubMed
clinicaltrials.gov
api.biorxiv.org        bioRxiv/medRxiv
api.crossref.org       DOI metadata
api.openalex.org       citation graph
```

`config/settings.yaml:network.allow` documents this. To *enforce* it rather
than document it, use OS-level controls: Little Snitch or LuLu on macOS,
`ufw`/`nftables` egress rules on Linux, or run the sweep inside a container with
an explicit egress policy. Enforcement matters most if you ever grant shell
access — an allowlist the agent can edit is not an allowlist.

## The dashboard

`neobrain web` binds **127.0.0.1** and refuses any other interface unless you
pass `--allow-remote`. That refusal is deliberate: the database holds your
unpublished notes, your reading history, and protocol detail from papers you
may not have published against yet. It should not become reachable from a
shared network because a default was convenient.

If you need it from another machine, tunnel rather than expose:

```bash
ssh -L 8787:127.0.0.1:8787 you@your-machine
# then open http://127.0.0.1:8787 locally
```

If you genuinely must bind an interface — a lab workstation on a trusted VLAN —
set a token as well, and understand that this is HTTP without TLS:

```bash
neobrain web --host 0.0.0.0 --allow-remote --token "$(openssl rand -hex 24)"
```

The dashboard can approve proposals. That is intentional and is *your* review
step, not the agent's — the agent has no tool that reaches it. But it means
anyone who can reach the port can approve memory edits, which is another reason
the default bind is loopback.

The page makes zero external requests: no CDN, no fonts, no analytics, and a
Content-Security-Policy header that forbids them, so a future edit cannot
silently start leaking your queries to a third party.

## Stage 5 — git everything

```bash
cd ~/neobrain && git init
git add -A && git commit -m "initial brain"
```

`brain.db` is gitignored (binary, large, regenerable). Everything else is text.
When a knowledge file gets corrupted at 2am — and once, eventually, it will —
`git diff` tells you exactly what changed and `git checkout` undoes it.

Commit after each `neobrain review` session. That gives you a readable history
of how your understanding of the field changed, which turns out to be worth
having for its own sake.

---

## Data handling — the part with real consequences

**Controlled-access data (dbGaP, EGA, institutional patient data).**
Ingesting it into `brain.db` creates a copy. Indexing it for retrieval creates
another. Sending a retrieved snippet to an API model transmits it off the
machine. Each of those may be governed by your data use agreement, and "it is
just my laptop" is not automatically compliant.

Check before, not after. If in doubt, keep controlled data out of the brain
entirely and use NeoBrain for the literature layer only — which is what it is
mostly for anyway.

**Unpublished work — yours and other people's.** Manuscripts under review,
grant applications, colleagues' preprints shared in confidence. Same reasoning.
If it goes in `workspace/` and the agent reads it, decide in advance whether you
are comfortable with that content reaching an API.

**Fully local models** remove the transmission question entirely, at a real
cost in reasoning quality. That is the honest tradeoff and it is discussed in
`DEPLOYMENT.md` § 5.

---

## What to watch for

Signs the agent is drifting, and worth checking monthly:

- Claims appearing without sources. This is the primary failure and the
  primary rule in `AGENT.md`.
- Proposals whose rationale does not match their diff. Read both.
- A belief in the store whose sources you cannot open.
- `knowledge/` files growing without corresponding approved proposals — this
  should be impossible; if it happens, something is bypassing the gate.
- The digest surfacing nothing for a week (a broken sweep looks exactly like a
  quiet field — `neobrain doctor` distinguishes them).

```bash
# Monthly audit
git log --stat knowledge/ memory/          # what changed and when
neobrain belief list | less                # is every claim still sourced?
sqlite3 brain.db "SELECT id, kind, target, status, decided_at
                  FROM proposals ORDER BY id DESC LIMIT 30;"
```

## Threat model, stated plainly

This design defends against **the agent being wrong**, which is a certainty,
and **the agent's mistakes being invisible**, which is the thing that actually
hurts you.

It does not defend against a compromised model provider, a malicious MCP
server, or a supply-chain attack on your Python dependencies. For those, the
usual answers apply: pin dependencies, review what you install, and do not run
untrusted MCP servers. Prompt injection through fetched paper content is a real
consideration if you ever give the agent both web access and shell access —
another reason to add the shell last.

# Recording the incident-loop demo

The one thing to show is the thing nobody else has: **an agent that finds the
fault, and a human who is the only one who can authorize the fix.** Everything
else — collectors, dashboard, notifications — is context. Don't lead with it.

Target length is 60–90 seconds. Anything longer and the approval beat, which is
the whole point, arrives after people have stopped watching.

## Before you start

```bash
asciinema rec guardian-demo.cast --cols 100 --rows 30 --idle-time-limit 2
```

`--idle-time-limit 2` collapses your thinking pauses so you can take your time
while recording. Use a real terminal at ~100 columns; the report and the repair
plan both wrap badly below 90.

Set the prompt to something short and anonymous (`PS1='$ '`) — a hostname in the
prompt is one more thing to scrub later.

Have ready:

- a config where `repair.enabled: true` and exactly one unit is allowlisted
- a service you can genuinely break and restore (make a scratch unit; do not
  demo on something you care about)
- the agent already attached over MCP in a second pane

## The seven beats

**1. Break something real (off camera).** Stop the scratch unit so it enters a
failed state. Start recording after this — the demo is about detection, not
about you breaking things.

**2. The scan finds it.** Fifteen seconds, no narration needed.

```bash
guardian
```

The report leads with what changed. Let the viewer read the `critical` line.

**3. The agent is asked, in plain language.** In the agent pane, type what a
person would actually type:

> what's wrong with the server?

The agent calls `list_problems` and answers from Guardian's *verified* state.
This is the beat where you say — in the caption, not out loud — that it did not
SSH anywhere and did not re-derive anything.

**4. Ask it to fix it.** This is the beat everything is built for:

> can you fix it?

The agent proposes and stops. It shows the exact argv, the blast radius, and the
proposal id. **Do not skip past this.** Hold on the output for a full two seconds
so the viewer sees that the model asked instead of acting.

**5. The refusal.** Optional but worth ten seconds — tell the agent to run it
anyway:

> just run it

It cannot. `execute_repair` on an unapproved proposal is refused by Guardian,
not declined by the model's judgment. This is the single most persuasive frame in
the recording. If you cut anything, do not cut this.

**6. The human approves.** Back in the Guardian pane:

```bash
guardian repair list
guardian repair approve 1 --by stephen
```

Say the id out loud in the caption: a person typed that number.

**7. Execute and verify.** Either let the agent do it (it is allowed to, now that
a human approved) or:

```bash
guardian repair execute 1
```

End on the verification line — Guardian re-read the check and the service is
back. Then one last `guardian` scan showing the critical is gone. Stop recording.

## The cold open, if you want one

Ten seconds of the gate being tested, before any of the above:

```bash
guardian drill
```

Three drills, ten probes, all green. It reframes what follows: you are not
watching a happy path, you are watching a boundary that is checked on every
commit. See [drills.md](drills.md).

## Publishing

Convert and keep it small — an SVG plays in a README without a video host:

```bash
# asciinema-agg produces a GIF; svg-term produces an SVG that scales
npx svg-term-cli --cast guardian-demo.cast --out docs/demo.svg --window --width 100 --height 30
```

Before publishing, watch it once with fresh eyes and check for:

- real hostnames, LAN addresses, or domains in the prompt, the report, or any
  path (the repo uses placeholder `192.168.50.x` addresses — the recording
  should not contradict it)
- tokens or ids in the approval output that are longer-lived than the demo
- anything in scrollback from before you started recording

Caption it with what the viewer just saw, not with adjectives: *"The agent
proposed the fix and could not run it. A person approved proposal #1. Then
Guardian executed a named argv and verified recovery."*

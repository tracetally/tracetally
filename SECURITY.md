# Security

tracetally reads trace files you pass on the command line and writes a summary
to the terminal and, when you ask, a report directory. It does not open
network sockets, send telemetry, or store provider API keys.

Prompt text is hashed at read and dropped. `tracetally report --include-content`
is the only switch that copies truncated prompt excerpts into the report.
`--redact` hashes trace, user, and session ids and the input paths, and it
suppresses those excerpts. The salt is not written into the report.

## Reporting a vulnerability

Open a private GitHub security advisory on this repository. Please include a
reproduction that uses synthetic data. Do not send production traces, prompts,
or API keys.

## What a report should not include

Prompt text, customer identifiers, and credentials. A synthetic JSONL snippet
that shows the bug is enough.

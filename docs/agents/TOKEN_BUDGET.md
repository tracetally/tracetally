# Token budget

Humans read this file. Bots do not open it on a turn.

## Why the bill grew

A measured session used about 156M tokens in two hours, and about 95% of that was cache reads. Every bot re-read a growing group chat, then answered with acknowledgements and summaries, which made the next read larger.

## Expected use

One task, one new chat, about 4 to 6 handoffs, with SPEC and DESIGN kept short:

- Expected: under **80,000** tokens for the task, cache reads included.
- Warning: **150,000** tokens, or **8** handoffs. Stop. Ask the human.
- Hard stop: **10** handoffs, **300,000** tokens, **8** tool calls in one turn, or a third pass between the same two roles.

A similar two-hour session should stay under **31M** tokens (at least 80% under 156M). That holds only if each task is a fresh short chat. One long shared chat will climb back up.

## Tool cap: 8 per turn

A normal turn is four calls: read `STATUS.md`, read the one file in `FILES`, write the owned file, update `STATUS.md` and `HANDOFFS.md`. Quality or Devops may run one command and retry it once. Call 8 must save the files. Then stop, even if the work is unfinished, and ask the human.

## When a limit is hit

1. Do not hand off to another bot.
2. Set `Blockers` in `STATUS.md` to the limit that fired.
3. Send one message: `TO: human`, the limit, and the decision you need.
4. Other bots stay silent.
5. Continue only in a new chat after the human answers.

Check the host usage meter for the chat. Do not spend a turn measuring tokens.

## If every bot still wakes up

"Stay silent" avoids a reply and the turns that follow. It does not skip the cache read when the host still invokes all five bots. @-mention only the role in `TO:`. Do not @-mention the group. Keep each chat message to the four handoff lines so the prefix stays small.

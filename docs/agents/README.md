# Agent handoff

Humans read this file. Do not paste it into a bot. Paste only that bot's role file.

The five bots are not configured in this repository. There is no `.cursor/rules`, `AGENTS.md`, prompt file, or orchestration script here. tracetally is an offline trace auditor; it does not run these bots.

| Role | Paste into the bot | Model | Writes |
|---|---|---|---|
| Product Manager | `product-manager.md` | Auto | `SPEC.md` |
| Architect | `architect.md` | Highest-reasoning model | `DESIGN.md` |
| Quality | `quality.md` | Auto | `Result` in `STATUS.md` |
| Delivery Manager | `delivery-manager.md` | Auto | `STATUS.md` |
| Devops | `devops.md` | Auto | `Result` in `STATUS.md` |

No model picker lives in this repo. Set the model on each bot in Cursor. Only Architect uses the high-reasoning model. The other four stay on Auto.

Order: Product Manager → Architect → Quality → Delivery Manager → Devops. Quality runs only when `Code to test` is yes. Devops runs only when `Code to deploy` is yes. These bots do not write application code. After code is in the tree, set `Code to test` and hand to Quality. After there is a build to ship, set `Code to deploy` and hand to Devops.

Shared files in this directory are the memory. Chat is only the four-line handoff. `TOKEN_BUDGET.md` is for humans.

Start one new chat per task. Mention only the bot named in `TO:`. That bot reads `STATUS.md` and the file named in `FILES`. It does not read the old chat.

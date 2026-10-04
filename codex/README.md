# Codex allowance notifications

Codex privately links each participating Discord member to their own ChatGPT account
through the official Codex CLI device-code flow. It reads live Codex allowance windows
from Codex app-server and sends optional low-allowance DMs.

No website, callback listener, inbound port, copied token, browser cookie, API key, or
manual usage estimate is required. The bot makes outbound HTTPS connections only.

## Bot-owner setup

Install or update the official standalone Codex CLI inside this cog's private Red data
directory:

    [p]codexset install

The installer is downloaded from https://chatgpt.com/codex/install.sh, bounded to 512 KiB,
and checked for the expected official release source before execution. The bot host must
allow outbound HTTPS and execution from Red's data directory.

## Member commands

- [p]codex connect - receive an OpenAI authorization link and one-time device code by DM.
- [p]codex - privately show live used/remaining percentages and reset times.
- [p]codex threshold PERCENT - set the remaining allowance warning threshold.
- [p]codex pause or resume - control automatic DMs.
- [p]codex disconnect true - revoke and delete the local account connection.
- [p]codex about - explain the connection and data boundary.

The default threshold is 20 percent remaining. The cog polls connected accounts every
15 minutes and sends each window warning once per reset period and threshold.

## Privacy and storage

Each Discord user receives a separate private Codex home directory with mode 0700. Codex
stores its renewable ChatGPT authorization there so background checks can run while the
member is offline. Account credentials are never placed in Red Config, Discord messages,
URLs, logs, source control, or another member's process environment.

Red Config stores only the Discord user ID, connection state, notification preference,
threshold, sent-alert keys, and last-check time. The cog does not read or store prompts,
conversations, source code, or OpenAI API keys. Disconnect and Red user-data deletion
attempt remote logout, remove the user's private Codex home, and clear their configuration.

## Live data source

Codex app-server's account/rateLimits/read response supplies each available limit name,
used percentage, window duration, and reset timestamp. Remaining percentage is calculated
as 100 minus the reported used percentage. Both legacy single-bucket and current
multi-bucket responses are supported.

Official references:

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions

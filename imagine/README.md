# Imagine

Imagine is a private, provider-neutral AI image-generation cog for Red. Its objective is to let a
bot owner connect trusted image backends once, while each allowlisted Discord server controls which
provider, channel, roles, users, cooldown, and daily allowance may use them.

The cog is default-deny at every level: the bot-wide switch must be on, the bot owner must allowlist
the server, a server administrator must enable Imagine, and the requester must be the bot owner or
have an explicit user or role grant. Generated files are posted directly to Discord and prompts run
one at a time per server.

## Provider status

- **OpenAI API:** text-to-image is available using an API key stored in Red shared API tokens. API
  usage is billed separately from ChatGPT and Codex subscriptions.
- **Codex:** text-to-image is available through a bot-managed Codex CLI linked to a ChatGPT account.
  Installation, updates, device-code login, status, usage, and disconnect controls are available in
  Discord to the Red bot owner.
- **ComfyUI:** the provider boundary and trusted endpoint staging exist, but workflow mapping,
  submission, polling, and output retrieval are not implemented yet.

## Codex setup from Discord

Codex does not need to be installed globally and the bot owner does not need SSH or root access.
Imagine installs the official standalone CLI inside its private Red data directory and keeps the
Codex authentication cache there.

First reload Imagine after installing or updating the cog:

```text
[p]reload imagine
```

As a Red bot owner, open the private lifecycle panel:

```text
[p]imagineset codex
```

Use the buttons in this order:

1. **Install / update Codex** downloads and verifies the official installer, then installs the CLI
   into Imagine's data directory.
2. **Link ChatGPT** privately displays an OpenAI device-authorization link and one-time code. Open
   the link, enter the code, and approve the account.
3. **Connection status** confirms the managed CLI version, account connection, and plan when those
   details are returned.
4. **Usage** privately displays available Codex limit windows, reset times, and account token
   summaries.
5. **Disconnect** removes the Codex login from this managed installation.

The host must permit outbound HTTPS and execution from Red's data directory. Authorization codes
and account details are shown only to the bot owner who pressed the button.

### Allow and configure a server

The Red bot owner must allowlist each Discord server once:

```text
[p]imagineset server allow
```

A server administrator can then enable Imagine, select Codex, and grant access to any combination
of roles and individual members:

```text
[p]imagineset enable
[p]imagineset provider codex
[p]imagineset role add @ImageCreator
[p]imagineset role add @VIP
[p]imagineset user add @SpecificUser
[p]imagineset channel #image-lab
[p]imagineset limits 10 60
[p]imagineset status
```

`limits 10 60` means ten successful images per member per rolling day with a 60-second cooldown.
Use `0` as the daily value for no per-user daily limit. Omitting the channel from
`[p]imagineset channel` clears the channel restriction.

Generate an image with:

```text
[p]imagine A tiny robot tending a rooftop garden at sunset, detailed digital art
```

Each successful Codex result includes input, cached-input, output, and reasoning token counts for
that Codex turn when the CLI reports them. These are diagnostics, not an exact image-credit count.
Codex built-in image generation counts against the linked account's general Codex allowance.

To remove access or stop generation:

```text
[p]imagineset role remove @ImageCreator
[p]imagineset user remove @SpecificUser
[p]imagineset disable
```

The bot owner also has emergency controls:

```text
[p]imagineset providerstate codex false
[p]imagineset global false
[p]imagineset server remove
```

## OpenAI API setup

Store the API credential in Red shared API tokens, allow and configure the server, and select the
OpenAI provider:

```text
[p]set api openai api_key,YOUR_OPENAI_API_KEY
[p]imagineset server allow
[p]imagineset enable
[p]imagineset provider openai
[p]imagineset role add @ImageCreator
[p]imagineset channel #image-lab
[p]imagineset limits 10 60
[p]imagine A tiny robot tending a rooftop garden at sunset
```

The API key stays in Red shared API tokens rather than Imagine's configuration. Never paste it into
a normal Discord message or commit it to the repository.

## Security model

Codex generation runs `codex exec` ephemerally in a new empty temporary directory with a
workspace-write sandbox, ignored user configuration and repository rules, a restricted environment,
a bounded timeout, and validated image output. Imagine does not forward unrelated bot secrets to
the Codex process. The Red bot owner can disable a provider or all generation without altering each
server's saved access configuration.

## ComfyUI staging

A bot owner may stage a trusted base URL:

```text
[p]imagineset comfyui http://127.0.0.1:8188
```

This does not enable generation yet. Do not expose an unauthenticated ComfyUI instance directly to
the public internet; use HTTPS or a trusted private network when remote workflow support is added.

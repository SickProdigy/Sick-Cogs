# Imagine

Imagine is a default-deny Red cog for AI image generation. OpenAI generation works now; ComfyUI
has a provider boundary and owner-only endpoint staging ready for workflow mapping.

## Private OpenAI setup

```text
[p]set api openai api_key,YOUR_OPENAI_API_KEY
[p]imagineset server allow
[p]imagineset enable
[p]imagineset user add @YourName
[p]imagineset channel #image-lab
[p]imagine draw a tiny robot tending a rooftop garden
```

You can add multiple users and roles. Review access with `[p]imagineset status`; stop generation
with `[p]imagineset disable`. The key stays in Red shared API tokens, not cog configuration.
OpenAI API usage is billed separately from ChatGPT and Codex subscriptions.

## Experimental Codex provider

Codex can use a bot-managed CLI and a ChatGPT device-code login. The bot owner does not need SSH, root access, or a system-wide Codex installation. Open the owner-only setup panel:

```text
[p]imagineset codex
```

Use **Install / update Codex**, then **Link ChatGPT**. The linking URL, one-time code, account status, and plan are shown only to the bot owner who presses the button. The managed CLI and its authentication cache stay in Imagine's private Red data directory. The same panel provides connection status, updates, disconnect controls, and a private **Usage** view with current rate-limit windows, reset times, and available account token summaries. Each generated Codex image also reports the input, cached, output, and reasoning tokens used by that Codex turn; those token counts are diagnostics, not a count of image credits.

Codex is available by default as a provider. After linking, a server administrator can select it inside an explicitly allowlisted and enabled server:

```text
[p]imagineset provider codex
[p]imagineset role add @VIP
[p]imagineset role add @Admin
[p]imagineset user add @SpecificUser
[p]imagine draw a tiny robot tending a rooftop garden
```

Codex follows Imagine's normal user and role allowlists. The bot owner can still use `[p]imagineset providerstate codex false` as an emergency kill switch. It runs `codex exec` ephemerally in a new empty temporary directory with a workspace-write sandbox, a restricted environment, and a five-minute timeout. The host must permit outbound HTTPS and execution from Red's data directory.

## ComfyUI staging

```text
[p]imagineset comfyui http://127.0.0.1:8188
```

Workflow mapping, polling, and output retrieval are next. Do not expose an unauthenticated ComfyUI
instance directly to the public internet.

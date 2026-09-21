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

Codex can use a cached ChatGPT login and its included image-generation allowance. Install Codex
for the same operating-system account that runs Red, then log that account in interactively:

```text
codex login
codex login status
```

The bot owner can enable it globally, then a server administrator can select it inside an
explicitly allowlisted server:

```text
[p]imagineset providerstate codex true
[p]imagineset provider codex
[p]imagineset role add @VIP
[p]imagineset role add @Admin
[p]imagineset user add @SpecificUser
[p]imagine draw a tiny robot tending a rooftop garden
```

Codex follows Imagine's normal user and role allowlists. The bot owner retains the global provider
switch, while server administrators decide which trusted members may use the selected provider. It runs
`codex exec` ephemerally in a new empty temporary directory with a workspace-write sandbox, a
restricted environment, and a five-minute timeout. It is experimental: OpenAI documents built-in
Codex image generation and non-interactive Codex separately, but recommends the Images API for
programmatic image generation. Never share or copy Codex's cached authentication file into Red.

## ComfyUI staging

```text
[p]imagineset comfyui http://127.0.0.1:8188
```

Workflow mapping, polling, and output retrieval are next. Do not expose an unauthenticated ComfyUI
instance directly to the public internet.

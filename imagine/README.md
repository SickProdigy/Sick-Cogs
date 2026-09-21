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

## ComfyUI staging

```text
[p]imagineset comfyui http://127.0.0.1:8188
```

Workflow mapping, polling, and output retrieval are next. Do not expose an unauthenticated ComfyUI
instance directly to the public internet.

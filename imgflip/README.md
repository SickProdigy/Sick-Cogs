# Imgflip

Imgflip generates memes through the [Imgflip API](https://imgflip.com/api).

The cog can list popular templates, accept Imgflip template names or template IDs, and send generated meme URLs back to Discord.

## Bot Owner Setup

- `[p]imgflipset` - Open the credential setup view for an Imgflip username and password.

Credentials are stored in Red shared API tokens for the `imgflip` service.

## Commands

- `[p]getmemes` - List popular Imgflip meme templates. Alias: `[p]listmemes`.
- `[p]meme <template> <text>` - Generate a meme from a template name or Imgflip template ID.

Separate caption boxes with `|`:

```text
meme Drake Hotline Bling top text | bottom text
```

Caption text can include optional formatting tokens:

```text
meme 181913649 first box color=#ff00ff | second box outline_color=#f1c40f font=arial
```

This cog does not persistently store user data. Template IDs and submitted caption text are sent to Imgflip to generate images.

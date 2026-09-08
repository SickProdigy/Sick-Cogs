# Meme

Meme creates static images locally and sends normal Discord attachments. It does not use ImgFlip, require API credentials, or upload captions and images to a meme provider.

## Commands

- `[p]meme list [search]` - List bundled original templates.
- `[p]meme <template> <top text> | <bottom text>` - Caption a bundled template.
- `[p]meme avatar @user <top text> | <bottom text>` - Caption a user's displayed avatar.
- `[p]memeversion` - Show the installed version.

Examples:

```text
[p]meme better-choice old way | new way
[p]meme paperwork another small task | the backlog
[p]meme avatar @user when the deploy | actually works
```

Bundled templates:

- `better-choice` - A two-panel reaction scene.
- `paperwork-escalation` - A three-stage office-chaos scene.

The bundled artwork is original to this repository. Caption text, avatars, and generated images are held in memory only for the current command.

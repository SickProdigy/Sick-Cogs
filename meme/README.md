# Meme

Meme finds online memes and GIFs through replaceable third-party providers, publishes optional scheduled channel feeds, and creates static captioned images locally. Online results retain a link to their original source.

## Online commands

- `[p]meme` / `[p]memes` - Post a random Meme API image or GIF from `r/memes`.
- `[p]meme community [subreddit]` - Get a Meme API result from a specific subreddit (`reddit` is an alias).
- `[p]meme search <query>` - Search public Imgur posts.
- `[p]meme imgur [query]` - Post a public Imgur result.
- `[p]meme gif [query]` - Post an animated Imgur result.
- `[p]meme sources` - Show which online providers are configured.

## Local caption commands (`memeify` / `imgflip`)

- `[p]memeify <template> <top> | <bottom>` - Caption a bundled original template.
- `[p]memeify list [search]` - List bundled templates.
- `[p]memeify avatar [member] <top> | <bottom>` - Caption a Discord avatar.
- `[p]memeify image <top> | <bottom>` - Caption an image attached to the command.

`[p]imgflip` is an alias for `[p]memeify`, including all of its subcommands.

The bundled artwork is original to this repository. Input images and generated images are held in memory only for the current command. Animated GIF captioning is not included yet; GIF discovery and posting are supported.

## Scheduled feeds

Administrators can publish Meme API or Imgur results automatically.

Simple Meme API setup:

```text
[p]memeset autopost
[p]memeset autopost pcmasterrace 180
```

The optional community defaults to `memes`; the optional interval defaults to 360 minutes (six hours).

Simple animated Imgur setup:

```text
[p]memeset gifpost #gifs gaming 240
```

Advanced setup and management:

```text
[p]memeset feed add #memes memeapi memes 360
[p]memeset feed add #gaming-memes memeapi pcmasterrace 180
[p]memeset feed add #gifs imgur-gif gaming 240
[p]memeset feed list
[p]memeset feed remove <feed-id>
```

The final number is the interval in minutes; the minimum is 30. The cog remembers recently posted provider IDs to avoid repeats. Reddit NSFW posts are only allowed in Discord channels marked NSFW.

For Meme API results, the original full-resolution media URL is preferred. If it is absent, the cog falls back to the last—and therefore largest—available preview image.

## Provider setup

Provider credentials are optional and are stored using Red shared API tokens:

```text
[p]set api imgur client_id,<client-id>
[p]set api reddit client_id,<client-id> client_secret,<client-secret>
```

Meme API is the default no-key community provider and is an independent service that is not affiliated with Reddit. Imgur provides optional public search and animated-GIF results. Direct Reddit OAuth remains available as a future provider if the bot owner receives official access.

# Dictionary

A maintained English dictionary cog for Red-DiscordBot, powered by the keyless [Free Dictionary API](https://dictionaryapi.dev/).

## Commands

Look up complete entries with definitions grouped by part of speech, examples, pronunciation text/audio, synonyms, antonyms, and source links:

```text
[p]dictionary hello
[p]dict hello
[p]define hello
```

Request only related words:

```text
[p]synonym happy
[p]synonyms happy
[p]antonym happy
[p]antonyms happy
```

Look up community-written slang separately through Urban Dictionary:

```text
[p]urban no cap
[p]urbandictionary no cap
[p]ud no cap
```

Urban Dictionary entries are submitted by the public and may be inaccurate or offensive. Up to five of the highest-scoring results are shown one at a time with Previous and Next buttons. Each result includes its example, author, vote totals, and a link to the original entry.

Running `[p]dictionary` without a term displays command help.

## Configuration and data

No API key or server configuration is required. Standard lookups are sent to `api.dictionaryapi.dev`; slang lookups are sent to `api.urbandictionary.com`. Both use HTTPS. The cog keeps no lookup history and stores no user or server data.

Results depend on the provider's English-language dataset. Some entries may not contain examples, pronunciation audio, synonyms, or antonyms.

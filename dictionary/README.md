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
[p]syn happy
[p]antonym happy
[p]antonyms happy
[p]ant happy
```

Running `[p]dictionary` without a term displays Red's standard help card, including the related definition, synonym, antonym, and Urban Dictionary commands.

Red's General cog already provides `[p]urban`; Dictionary deliberately does not register a duplicate Urban Dictionary command.

## Configuration and data

No API key or server configuration is required. Lookups are sent to `api.dictionaryapi.dev` over HTTPS. The cog keeps no lookup history and stores no user or server data.

Results depend on the provider's English-language dataset. Some entries may not contain examples, pronunciation audio, synonyms, or antonyms.

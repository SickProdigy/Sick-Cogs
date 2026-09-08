# Contributing to Sick-Cogs

Thanks for helping improve Sick-Cogs. This repository contains Red-DiscordBot cogs maintained for SickProdigy's Discord projects.

## How to Contribute

1. Fork the repository and clone your fork.
2. Create a focused branch for the change:

   ```bash
   git checkout -b feature/your-change
   ```

3. Make the smallest practical change that solves the issue.
4. Test the affected cog when possible.
5. Commit with a clear message and reference the related issue when there is one.
6. Push your branch and open a pull request.

## Code Style

- Follow the style already used by the cog you are editing.
- Keep Red-DiscordBot compatibility in mind and avoid changing stored config formats unless there is a migration path.
- Add or update docstrings when the behavior is not obvious.
- Keep user-facing messages clear, concise, and useful in Discord.
- Do not commit secrets, tokens, API keys, `.env` files, local Red runtime data, or generated caches.

## Red Cog Expectations

- Each cog should include a valid `info.json`.
- Keep `requirements` limited to packages the cog truly needs.
- Include an `end_user_data_statement` for Red compliance.
- Prefer async HTTP clients and Red utilities already used in the repo.
- Test runtime behavior in an isolated Red-DiscordBot environment before release.

## Issues

- Search existing issues before opening a new one.
- Include the cog name, command used, expected behavior, actual behavior, and any relevant traceback.
- For API/SOAP integrations, describe the response code or sanitized error message, but do not paste secrets.

## Pull Requests

- Keep pull requests focused on one cog or one workflow when possible.
- Mention any manual testing performed.
- Call out new dependencies, config changes, or behavior changes in the PR description.

## License

By contributing, you agree that your contributions will be licensed under this repository's license.

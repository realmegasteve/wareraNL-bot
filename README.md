# WareraNL Bot

Minimal Discord bot used in this repository. This README covers quick setup, configuration, and common commands for development and deployment.

## Prerequisites

- Python 3.8+ (recommended)
- pip

## Quick setup

1. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. Add configuration and API keys:

- Place your credentials in `_api_keys.json` and your runtime config in `config.json` at the project root. Keep these files secret and out of version control.

## Running the bot

- Run locally:

```bash
python bot.py
```

- Using systemd (restart example):

```bash
sudo systemctl restart discord-bot.service
```

## Development

- Use the files under the `cogs/` directory to add or modify bot features.
- See `scripts/run_poll_once.py` for an example script used by the project.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines and code style.

## License

See [LICENSE.md](LICENSE.md).

## Notes

- Keep `_api_keys.json` and any secrets out of the repository. Use environment-specific secrets management in production.
- If you want, I can also add example config templates or a more detailed setup guide—tell me what you'd like next.


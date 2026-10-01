"""Repository-wide pytest setup, loaded before any test module imports the app.

Settings read ``.env`` from the working directory by default. A fixture once
passed on a developer's machine only because their ``.env`` supplied a signing
secret, then failed in CI, which has no ``.env``. An empty ``AURI_ENV_FILE``
makes both the backend and the bot settings ignore any env file, so tests see
only the environment CI sees.
"""

import os

os.environ["AURI_ENV_FILE"] = ""

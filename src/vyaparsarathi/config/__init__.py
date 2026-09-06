"""Configuration: environment loading and tunable constants.

Nothing else in the codebase reads ``os.environ`` directly (CLAUDE.md §24).
"""

from vyaparsarathi.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]

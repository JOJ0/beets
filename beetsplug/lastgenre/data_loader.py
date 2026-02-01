# This file is part of beets.
# Copyright 2016, Adrian Sampson.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.

"""Data loading for the lastgenre plugin."""

from __future__ import annotations

import configparser
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Pattern

import yaml

from beets.ui import UserError
from beets.util import normpath

from .canonicalization import flatten_tree
from .config import ALIASES, C14N_TREE, WHITELIST


class WhitelistLoader:
    """Loads genre whitelist from a text file."""

    def __init__(self, filename: str | bool | None, apply_aliases=None):
        """Initialize whitelist loader.

        Args:
            filename: Path to whitelist file, True for default, or None
            apply_aliases: Optional function to apply aliases to loaded genres
        """
        self.filename = filename
        self.apply_aliases = apply_aliases

    def load(self) -> set[str]:
        """Load whitelist from file."""
        whitelist = set()
        wl_filename = self.filename

        if wl_filename in (True, "", None):
            wl_filename = WHITELIST

        if wl_filename:
            text = Path(wl_filename).expanduser().read_text(encoding="utf-8")
            for line in text.splitlines():
                if (line := line.strip().lower()) and not line.startswith("#"):
                    whitelist.add(line)

        if whitelist and self.apply_aliases:
            whitelist_list = list(whitelist)
            aliased_whitelist = self.apply_aliases(whitelist_list)
            whitelist = set(aliased_whitelist)

        return whitelist


class C14NTreeLoader:
    """Loads canonicalization tree from YAML file."""

    def __init__(
        self,
        filename: str | bool,
        prefer_specific: bool = False,
        apply_aliases=None,
    ):
        """Initialize tree loader.

        Args:
            filename: Path to tree file, True for default, or False to disable
            prefer_specific: Whether prefer_specific mode is enabled
            apply_aliases: Optional function to apply aliases to tree genres
        """
        self.filename = filename
        self.prefer_specific = prefer_specific
        self.apply_aliases = apply_aliases

    def load(self) -> tuple[list[list[str]], bool]:
        """Load canonicalization tree.

        Returns:
            Tuple of (branches, canonicalize_enabled)
        """
        c14n_branches: list[list[str]] = []
        c14n_filename = self.filename
        canonicalize = c14n_filename is not False

        # Default tree
        if c14n_filename in (True, "", None) or (
            not canonicalize and self.prefer_specific
        ):
            c14n_filename = C14N_TREE

        # Read the tree
        if c14n_filename:
            with Path(c14n_filename).expanduser().open(encoding="utf-8") as f:
                genres_tree = yaml.safe_load(f)
            flatten_tree(genres_tree, [], c14n_branches)

            if c14n_branches and self.apply_aliases:
                for i, branch in enumerate(c14n_branches):
                    c14n_branches[i] = self.apply_aliases(branch)

        return c14n_branches, canonicalize


class AliasLoader:
    """Loads genre aliases from INI file."""

    def __init__(self, filename: str | bool | None):
        """Initialize alias loader.

        Args:
            filename: Path to aliases file, True for default, or None
        """
        self.filename = filename

    def load(self) -> list[dict[str, str]]:
        """Load aliases from file.

        Returns:
            List of pattern/replacement dictionaries
        """
        aliases = []
        aliases_filename = self.filename

        if aliases_filename in (True, "", None):
            aliases_filename = ALIASES

        if aliases_filename:
            aliases_filename = normpath(aliases_filename)
            try:
                config_parser = configparser.ConfigParser()
                config_parser.read(aliases_filename, encoding="utf-8")
                for section in config_parser.sections():
                    aliases.extend(
                        {pattern: replacement}
                        for pattern, replacement in config_parser[
                            section
                        ].items()
                    )
            except Exception:
                raise

        return aliases


class BlacklistLoader:
    """Loads genre blacklist from custom format file."""

    def __init__(self, filename: str | bool):
        """Initialize blacklist loader.

        Args:
            filename: Path to blacklist file or False to disable
        """
        self.filename = filename

    def load(self) -> dict[str, list[Pattern[str]]]:
        """Load blacklist from file.

        Returns:
            Dictionary mapping artist names to compiled regex patterns.
            Special '*' key for global forbidden genres.

        Raises:
            UserError: if the file format is invalid.
        """
        blacklist = defaultdict(list)
        if not self.filename:
            return blacklist

        section = None
        with Path(self.filename).expanduser().open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.lower()
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                if not line.startswith(" "):
                    # Section header
                    if not line.rstrip().endswith(":"):
                        raise UserError(
                            f"Malformed blacklist section header "
                            f"at line {lineno}: {line}"
                        )
                    section = line.rstrip(":\r\n")
                else:
                    # Pattern line: must be indented
                    if section is None:
                        raise UserError(
                            f"Blacklist regex pattern line before any section header "
                            f"at line {lineno}: {line}"
                        )
                    blacklist[section].append(line.strip())

        # Compile regex patterns
        compiled_blacklist = defaultdict(list)
        for artist, patterns in blacklist.items():
            compiled_patterns = []
            for pattern in patterns:
                try:
                    compiled_patterns.append(re.compile(pattern, re.IGNORECASE))
                except re.error:
                    # Treat as literal string if regex fails
                    escaped_pattern = re.escape(pattern)
                    compiled_patterns.append(
                        re.compile(escaped_pattern, re.IGNORECASE)
                    )
            compiled_blacklist[artist] = compiled_patterns
        return compiled_blacklist

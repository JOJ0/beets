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

"""Configuration and constants for the lastgenre plugin."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# Default file paths
WHITELIST = os.path.join(os.path.dirname(__file__), "genres.txt")
C14N_TREE = os.path.join(os.path.dirname(__file__), "genres-tree.yaml")
ALIASES = os.path.join(os.path.dirname(__file__), "aliases.ini")


@dataclass
class LastGenreConfig:
    """Configuration for the lastgenre plugin."""

    whitelist: str | bool | None = True
    min_weight: int = 10
    count: int = 1
    fallback: str | None = None
    canonical: str | bool = False
    source: str = "album"
    force: bool = False
    keep_existing: bool = False
    auto: bool = True
    separator: str = ", "
    prefer_specific: bool = False
    title_case: bool = True
    pretend: bool = False
    blacklist: str | bool = False
    aliases: str | bool = True

    # Computed fields
    sources: tuple[str, ...] = field(init=False)

    def __post_init__(self):
        """Process configuration after initialization."""
        # Parse source configuration
        if isinstance(self.source, str):
            self.sources = tuple(s.strip() for s in self.source.split(","))
        else:
            self.sources = (self.source,)

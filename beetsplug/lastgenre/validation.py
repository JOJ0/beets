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

"""Genre validation for the lastgenre plugin."""

from __future__ import annotations

from typing import Pattern


class GenreValidator:
    """Validates genres against whitelist and blacklist."""

    def __init__(
        self,
        whitelist: set[str] | None,
        blacklist: dict[str, list[Pattern[str]]],
    ):
        """Initialize validator.

        Args:
            whitelist: Set of allowed genres, or None to allow all
            blacklist: Dict mapping artist names to forbidden regex patterns
        """
        self.whitelist = whitelist
        self.blacklist = blacklist

    def is_valid(self, genre: str) -> bool:
        """Check if genre is in whitelist (if whitelist is enabled)."""
        if genre and (not self.whitelist or genre.lower() in self.whitelist):
            return True
        return False

    def is_forbidden(self, genre: str, artist: str | None = None) -> bool:
        """Check if genre is forbidden for the given artist.

        Args:
            genre: Genre to check
            artist: Artist name (optional) for artist-specific blacklist

        Returns:
            True if genre is forbidden
        """
        if not self.blacklist:
            return False

        genre = genre.lower()

        # Check global forbidden patterns
        if "*" in self.blacklist:
            for pattern in self.blacklist["*"]:
                if pattern.search(genre):
                    return True

        # Check artist-specific forbidden patterns
        if artist:
            artist = artist.lower()
            if artist in self.blacklist:
                for pattern in self.blacklist[artist]:
                    if pattern.search(genre):
                        return True

        return False

    def filter_genres(
        self, genres: list[str], artist: str | None = None
    ) -> list[str]:
        """Filter genres by validity and blacklist.

        Args:
            genres: List of genres to filter
            artist: Artist name for blacklist checking

        Returns:
            Filtered list of genres
        """
        return [
            g
            for g in genres
            if self.is_valid(g) and not self.is_forbidden(g, artist)
        ]

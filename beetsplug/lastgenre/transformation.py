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

"""Genre transformation for the lastgenre plugin."""

from __future__ import annotations

import re


class GenreTransformer:
    """Transforms genres using aliases and formatting."""

    def __init__(self, aliases: list[dict[str, str]] | None = None):
        """Initialize transformer.

        Args:
            aliases: List of pattern/replacement dictionaries
        """
        self.aliases = aliases or []

    def apply_aliases(self, genres: list[str]) -> list[str]:
        """Apply regex aliases to the genre list.

        Returns lowercase genres to maintain consistency with the rest
        of the genre system.
        """
        if not self.aliases or not genres:
            return genres

        result = genres.copy()
        for alias_pair in self.aliases:
            for pattern, replacement in alias_pair.items():
                try:
                    result = [
                        re.sub(pattern, replacement, genre, flags=re.IGNORECASE)
                        for genre in result
                    ]
                except re.error:
                    # Skip invalid patterns
                    continue
        return [g.lower() for g in result]

    def format_genres(
        self, genres: list[str], title_case: bool, separator: str
    ) -> str:
        """Format genres to title case if needed and join with separator."""
        if title_case:
            formatted = [tag.title() for tag in genres]
        else:
            formatted = genres
        return separator.join(formatted)

    @staticmethod
    def parse_existing_genres(
        genre_string: str, separators: tuple[str, ...] = (";", "/", ",")
    ) -> list[str]:
        """Parse existing genre string using configured separators.

        Args:
            genre_string: Genre string to parse
            separators: Tuple of separator characters

        Returns:
            List of individual genres
        """
        if not genre_string:
            return []

        # Try separators in order of preference
        for separator in separators:
            if separator in genre_string:
                item_genre = genre_string.split(separator)
                break
        else:
            # No separators found
            item_genre = [genre_string]

        # Filter out empty strings and strip whitespace
        return [g.strip() for g in item_genre if g.strip()]

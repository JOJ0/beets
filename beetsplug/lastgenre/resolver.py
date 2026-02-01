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

"""Genre resolution logic for the lastgenre plugin."""

from __future__ import annotations

from beets.util import unique_list

from .canonicalization import GenreCanonicalizer
from .transformation import GenreTransformer
from .validation import GenreValidator


class GenreResolver:
    """Resolves and processes genre lists."""

    def __init__(
        self,
        validator: GenreValidator,
        canonicalizer: GenreCanonicalizer | None,
        transformer: GenreTransformer,
        count: int = 1,
        prefer_specific: bool = False,
    ):
        """Initialize genre resolver.

        Args:
            validator: Genre validator for whitelist/blacklist
            canonicalizer: Optional canonicalizer for genre hierarchy
            transformer: Genre transformer for aliases
            count: Maximum number of genres to return
            prefer_specific: Whether to prefer more specific genres
        """
        self.validator = validator
        self.canonicalizer = canonicalizer
        self.transformer = transformer
        self.count = count
        self.prefer_specific = prefer_specific

    def resolve_genres(
        self, tags: list[str], artist: str | None = None
    ) -> list[str]:
        """Canonicalize, sort and filter a list of genres.

        Args:
            tags: Input genre tags
            artist: Artist name for blacklist filtering

        Returns:
            Filtered and processed list of genres
        """
        if not tags:
            return []

        # Apply aliases to input genres once at entry point
        if self.transformer.aliases:
            tags = self.transformer.apply_aliases(tags)

        # Canonicalization (if enabled)
        if self.canonicalizer:
            tags_all = []
            for tag in tags:
                # Add parents that are in the whitelist, or add the oldest ancestor
                if self.validator.whitelist:
                    parents = [
                        x
                        for x in self.canonicalizer.find_parents(tag)
                        if self.validator.is_valid(x)
                    ]
                else:
                    parents = [self.canonicalizer.find_parents(tag)[-1]]

                tags_all += parents

                # Stop if we have enough tags already
                if not self.prefer_specific and len(tags_all) >= self.count:
                    break
            tags = tags_all

        tags = unique_list(tags)

        # Sort by specificity if configured
        if self.prefer_specific and self.canonicalizer:
            tags = self.canonicalizer.sort_by_depth(tags)

        # Final validation
        valid_tags = self.validator.filter_genres(tags, artist)
        return valid_tags[: self.count]

    def combine_and_resolve(
        self, existing: list[str], new: list[str], artist: str | None = None
    ) -> list[str]:
        """Combine existing and new genres, then resolve.

        Args:
            existing: Existing genres from library
            new: New genres from Last.fm
            artist: Artist name for blacklist filtering

        Returns:
            Combined and resolved genre list
        """
        combined = existing + new
        return self.resolve_genres(combined, artist=artist)

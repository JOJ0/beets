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

"""Last.fm API client for the lastgenre plugin."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pylast

from beets import plugins


LASTFM = pylast.LastFMNetwork(api_key=plugins.LASTFM_KEY)

PYLAST_EXCEPTIONS = (
    pylast.WSError,
    pylast.MalformedResponseError,
    pylast.NetworkError,
)


class GenreCache:
    """Cache for Last.fm genre lookups."""

    def __init__(self):
        """Initialize empty cache."""
        self._cache: dict[str, list[str]] = {}

    def get(self, key: str) -> list[str] | None:
        """Get cached genres for key."""
        return self._cache.get(key)

    def set(self, key: str, genres: list[str]) -> None:
        """Cache genres for key."""
        self._cache[key] = genres

    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()


class LastFmClient:
    """Client for fetching genres from Last.fm API."""

    def __init__(self, cache: GenreCache | None = None):
        """Initialize client.

        Args:
            cache: Optional cache for genre lookups
        """
        self.cache = cache or GenreCache()

    def fetch_genre(
        self,
        lastfm_obj: pylast.Album | pylast.Artist | pylast.Track,
        min_weight: int,
    ) -> list[str]:
        """Fetch genres for a pylast entity.

        Args:
            lastfm_obj: Pylast Album, Artist, or Track object
            min_weight: Minimum tag weight to include

        Returns:
            List of genre strings (lowercase)
        """
        try:
            tags = [
                (tag.item.get_name().lower(), int(tag.weight))
                for tag in lastfm_obj.get_top_tags()
            ]
        except PYLAST_EXCEPTIONS:
            return []

        # Filter by minimum weight
        return [tag for tag, weight in tags if weight >= min_weight]

    def lookup_cached(
        self,
        entity: str,
        method: Callable[..., Any],
        min_weight: int,
        *args: str,
    ) -> list[str]:
        """Perform cached lookup for genres.

        Args:
            entity: Entity type (track, album, artist)
            method: Pylast method to call
            min_weight: Minimum tag weight
            *args: Arguments for the method

        Returns:
            List of genre strings (lowercase)
        """
        if any(not s for s in args):
            return []

        key = f"{entity}.{'-'.join(str(a) for a in args)}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        # Replace Unicode dash for better Last.fm results
        args_replaced = [a.replace("\u2010", "-") for a in args]
        genres = self.fetch_genre(method(*args_replaced), min_weight)
        self.cache.set(key, genres)
        return genres

    def fetch_album_genre(
        self, albumartist: str, albumtitle: str, min_weight: int
    ) -> list[str]:
        """Fetch genres for an album."""
        return self.lookup_cached(
            "album", LASTFM.get_album, min_weight, albumartist, albumtitle
        )

    def fetch_artist_genre(self, artist: str, min_weight: int) -> list[str]:
        """Fetch genres for an artist."""
        return self.lookup_cached(
            "artist", LASTFM.get_artist, min_weight, artist
        )

    def fetch_track_genre(
        self, trackartist: str, tracktitle: str, min_weight: int
    ) -> list[str]:
        """Fetch genres for a track."""
        return self.lookup_cached(
            "track", LASTFM.get_track, min_weight, trackartist, tracktitle
        )

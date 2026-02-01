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

"""Genre processing pipeline for the lastgenre plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING

from beets import config, library
from beets.library import Album
from beets.util import plurality

from .lastfm_client import LastFmClient
from .models import ProcessingResult
from .resolver import GenreResolver
from .transformation import GenreTransformer

if TYPE_CHECKING:
    from beets.library import LibModel


class GenrePipeline:
    """Main processing pipeline for genre resolution."""

    def __init__(
        self,
        client: LastFmClient,
        resolver: GenreResolver,
        transformer: GenreTransformer,
        sources: tuple[str, ...],
        min_weight: int = 10,
        force: bool = False,
        keep_existing: bool = False,
        fallback: str | None = None,
        title_case: bool = True,
        separator: str = ", ",
    ):
        """Initialize genre pipeline.

        Args:
            client: Last.fm API client
            resolver: Genre resolver
            transformer: Genre transformer
            sources: Tuple of allowed sources (track, album, artist)
            min_weight: Minimum tag weight for Last.fm tags
            force: Whether to force update existing genres
            keep_existing: Whether to keep existing genres
            fallback: Fallback genre string
            title_case: Whether to format genres in title case
            separator: Genre separator string
        """
        self.client = client
        self.resolver = resolver
        self.transformer = transformer
        self.sources = sources
        self.min_weight = min_weight
        self.force = force
        self.keep_existing = keep_existing
        self.fallback = fallback
        self.title_case = title_case
        self.separator = separator

    def get_genre(self, obj: LibModel) -> ProcessingResult:
        """Get the final genre string for an Album or Item object.

        Returns:
            ProcessingResult with genre string and label
        """
        keep_genres = []
        genres = self._get_existing_genres(obj)

        if genres and not self.force:
            # Return existing genres as-is without force
            if isinstance(obj, library.Item):
                genre_str = obj.get("genre", with_album=False)
            else:
                genre_str = obj.get("genre")
            return ProcessingResult(genre_str, "keep any, no-force")

        if self.force and self.keep_existing:
            keep_genres = [g.lower() for g in genres]

        # Try each source in order
        if result := self._try_track_stage(obj, keep_genres):
            return result
        if result := self._try_album_stage(obj, keep_genres):
            return result
        if result := self._try_artist_stage(obj, keep_genres):
            return result
        if result := self._try_original_fallback(obj, keep_genres):
            return result

        # Return configured fallback
        if self.fallback:
            return ProcessingResult(self.fallback, "fallback")

        return ProcessingResult(None, "fallback unconfigured")

    def _get_existing_genres(self, obj: LibModel) -> list[str]:
        """Get existing genres from the object."""
        if isinstance(obj, library.Item):
            genre_string = obj.get("genre", with_album=False)
        else:
            genre_string = obj.get("genre")

        return self.transformer.parse_existing_genres(
            genre_string, separators=(";", "/", ",")
        )

    def _resolve_and_format(
        self,
        stage_label: str,
        keep_genres: list[str],
        new_genres: list[str],
        artist: str | None,
    ) -> ProcessingResult | None:
        """Resolve genres for a stage and format result."""
        resolved = self.resolver.combine_and_resolve(
            keep_genres, new_genres, artist
        )
        if resolved:
            suffix = "whitelist" if self.resolver.validator.whitelist else "any"
            label = f"{stage_label}, {suffix}"
            if keep_genres:
                label = f"keep + {label}"
            genre_str = self.transformer.format_genres(
                resolved, self.title_case, self.separator
            )
            return ProcessingResult(genre_str, label, stage_label)
        return None

    def _try_track_stage(
        self, obj: LibModel, keep_genres: list[str]
    ) -> ProcessingResult | None:
        """Try to get genres from track source."""
        if not isinstance(obj, library.Item) or "track" not in self.sources:
            return None

        new_genres = self.client.fetch_track_genre(
            obj.artist, obj.title, self.min_weight
        )
        if new_genres:
            artist = obj.artist
            return self._resolve_and_format(
                "track", keep_genres, new_genres, artist
            )
        return None

    def _try_album_stage(
        self, obj: LibModel, keep_genres: list[str]
    ) -> ProcessingResult | None:
        """Try to get genres from album source."""
        if "album" not in self.sources:
            return None

        new_genres = self.client.fetch_album_genre(
            obj.albumartist, obj.album, self.min_weight
        )
        if new_genres:
            artist = obj.albumartist
            return self._resolve_and_format(
                "album", keep_genres, new_genres, artist
            )
        return None

    def _try_artist_stage(
        self, obj: LibModel, keep_genres: list[str]
    ) -> ProcessingResult | None:
        """Try to get genres from artist source."""
        if "artist" not in self.sources:
            return None

        new_genres = []
        stage_label = "artist"
        artist = None

        if isinstance(obj, library.Item):
            new_genres = self.client.fetch_artist_genre(
                obj.artist, self.min_weight
            )
            artist = obj.artist
        elif obj.albumartist != config["va_name"].as_str():
            # Album artist lookup
            new_genres = self.client.fetch_artist_genre(
                obj.albumartist, self.min_weight
            )
            artist = obj.albumartist
            stage_label = "album artist"

            # Try multi-valued albumartists if no results
            if not new_genres:
                for albumartist in obj.albumartists:
                    new_genres += self.client.fetch_artist_genre(
                        albumartist, self.min_weight
                    )
                if new_genres:
                    stage_label = "multi-valued album artist"
        else:
            # Various Artists - find most popular track genre
            return self._try_va_album_genre(obj, keep_genres)

        if new_genres:
            return self._resolve_and_format(
                stage_label, keep_genres, new_genres, artist
            )
        return None

    def _try_va_album_genre(
        self, obj: Album, keep_genres: list[str]
    ) -> ProcessingResult | None:
        """Get most popular genre for Various Artists albums."""
        item_genres = []
        for item in obj.items():
            item_genre = None
            if "track" in self.sources:
                item_genre = self.client.fetch_track_genre(
                    item.artist,
                    item.title,
                    self.min_weight,
                )
            if not item_genre:
                item_genre = self.client.fetch_artist_genre(
                    item.artist, self.min_weight
                )
            if item_genre:
                item_genres += item_genre

        if item_genres:
            most_popular, _ = plurality(item_genres)
            new_genres = [most_popular]
            # Use albumartist as artist for blacklist checking
            artist = obj.albumartist
            return self._resolve_and_format(
                "most popular track", keep_genres, new_genres, artist
            )
        return None

    def _try_original_fallback(
        self, obj: LibModel, keep_genres: list[str]
    ) -> ProcessingResult | None:
        """Try to keep original genre as fallback."""
        if not obj.genre or not self.keep_existing:
            return None

        # Check if original is valid
        if (
            not self.resolver.validator.whitelist
            or self.resolver.validator.is_valid(obj.genre.lower())
        ):
            return ProcessingResult(obj.genre, "original fallback")

        # Try to canonicalize original
        if keep_genres:
            artist = getattr(obj, "albumartist", None) or getattr(
                obj, "artist", None
            )
            return self._resolve_and_format(
                "original fallback", keep_genres, [], artist
            )
        return None

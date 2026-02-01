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

"""Gets genres for imported music based on Last.fm tags."""

from __future__ import annotations

from functools import singledispatchmethod
from typing import TYPE_CHECKING

from beets import library, plugins, ui
from beets.library import Album, Item

from .canonicalization import GenreCanonicalizer
from .data_loader import (
    AliasLoader,
    BlacklistLoader,
    C14NTreeLoader,
    WhitelistLoader,
)
from .lastfm_client import GenreCache, LastFmClient
from .pipeline import GenrePipeline
from .resolver import GenreResolver
from .transformation import GenreTransformer
from .validation import GenreValidator

if TYPE_CHECKING:
    import optparse

    from beets.library import LibModel


class LastGenrePlugin(plugins.BeetsPlugin):
    """Plugin to fetch and apply genres from Last.fm."""

    def __init__(self) -> None:
        super().__init__()
        self.config.add(
            {
                "whitelist": True,
                "min_weight": 10,
                "count": 1,
                "fallback": None,
                "canonical": False,
                "source": "album",
                "force": False,
                "keep_existing": False,
                "auto": True,
                "separator": ", ",
                "prefer_specific": False,
                "title_case": True,
                "pretend": False,
                "blacklist": False,
                "aliases": True,
            }
        )
        self.setup()

    def setup(self) -> None:
        """Initialize plugin dependencies."""
        if self.config["auto"]:
            self.import_stages = [self.imported]

        # Create transformer early for alias application during loading
        alias_loader = AliasLoader(self.config["aliases"].get())
        aliases = alias_loader.load()
        self.transformer = GenreTransformer(aliases)

        # Load data with alias support
        whitelist_loader = WhitelistLoader(
            self.config["whitelist"].get(),
            apply_aliases=self.transformer.apply_aliases if aliases else None,
        )
        whitelist = whitelist_loader.load()

        tree_loader = C14NTreeLoader(
            self.config["canonical"].get(),
            prefer_specific=self.config["prefer_specific"].get(bool),
            apply_aliases=self.transformer.apply_aliases if aliases else None,
        )
        c14n_branches, canonicalize = tree_loader.load()

        blacklist_loader = BlacklistLoader(self.config["blacklist"].get())
        blacklist = blacklist_loader.load()

        # Build components
        self.validator = GenreValidator(whitelist, blacklist)
        self.canonicalizer = (
            GenreCanonicalizer(c14n_branches) if canonicalize else None
        )
        self.resolver = GenreResolver(
            validator=self.validator,
            canonicalizer=self.canonicalizer,
            transformer=self.transformer,
            count=self.config["count"].get(int),
            prefer_specific=self.config["prefer_specific"].get(bool),
        )

        # Last.fm client
        self.client = LastFmClient(GenreCache())

        # Main pipeline
        self.pipeline = GenrePipeline(
            client=self.client,
            resolver=self.resolver,
            transformer=self.transformer,
            sources=self._parse_sources(),
            min_weight=self.config["min_weight"].get(int),
            force=self.config["force"].get(bool),
            keep_existing=self.config["keep_existing"].get(bool),
            fallback=self.config["fallback"].get(),
            title_case=self.config["title_case"].get(bool),
            separator=self.config["separator"].as_str(),
        )

    def _parse_sources(self) -> tuple[str, ...]:
        """Parse source configuration into tuple."""
        source = self.config["source"].as_choice(("track", "album", "artist"))
        if source == "track":
            return "track", "album", "artist"
        if source == "album":
            return "album", "artist"
        if source == "artist":
            return ("artist",)
        return tuple()

    def _fetch_and_log_genre(self, obj: LibModel) -> None:
        """Fetch genre and log it."""
        self._log.info(str(obj))
        result = self.pipeline.get_genre(obj)
        obj.genre = result.genre
        self._log.debug("Resolved ({}): {}", result.label, obj.genre)
        ui.show_model_changes(obj, fields=["genre"], print_obj=False)

    @singledispatchmethod
    def _process(self, obj: LibModel, write: bool) -> None:
        """Process an object."""
        raise NotImplementedError

    @_process.register
    def _process_track(self, obj: Item, write: bool) -> None:
        """Process a single track."""
        self._fetch_and_log_genre(obj)
        if not self.config["pretend"]:
            obj.try_sync(write=write, move=False)

    @_process.register
    def _process_album(self, obj: Album, write: bool) -> None:
        """Process an album."""
        self._fetch_and_log_genre(obj)
        if "track" in self.pipeline.sources:
            for item in obj.items():
                self._process(item, write)

        if not self.config["pretend"]:
            obj.try_sync(
                write=write,
                move=False,
                inherit="track" not in self.pipeline.sources,
            )

    def commands(self) -> list[ui.Subcommand]:
        """Add lastgenre CLI command."""
        lastgenre_cmd = ui.Subcommand("lastgenre", help="fetch genres")
        lastgenre_cmd.parser.add_option(
            "-p",
            "--pretend",
            action="store_true",
            help="show actions but do nothing",
        )
        lastgenre_cmd.parser.add_option(
            "-f",
            "--force",
            dest="force",
            action="store_true",
            help="modify existing genres",
        )
        lastgenre_cmd.parser.add_option(
            "-F",
            "--no-force",
            dest="force",
            action="store_false",
            help="don't modify existing genres",
        )
        lastgenre_cmd.parser.add_option(
            "-k",
            "--keep-existing",
            dest="keep_existing",
            action="store_true",
            help="combine with existing genres",
        )
        lastgenre_cmd.parser.add_option(
            "-K",
            "--no-keep-existing",
            dest="keep_existing",
            action="store_false",
            help="don't combine with existing genres",
        )
        lastgenre_cmd.parser.add_option(
            "-s",
            "--source",
            dest="source",
            type="string",
            help="genre source: artist, album, or track",
        )
        lastgenre_cmd.parser.add_option(
            "-A",
            "--items",
            action="store_false",
            dest="album",
            help="match items instead of albums",
        )
        lastgenre_cmd.parser.add_option(
            "-a",
            "--albums",
            action="store_true",
            dest="album",
            help="match albums instead of items (default)",
        )
        lastgenre_cmd.parser.set_defaults(album=True)

        def lastgenre_func(
            lib: library.Library, opts: optparse.Values, args: list[str]
        ) -> None:
            self.config.set_args(opts)
            self.setup()  # Reinitialize with new options

            method = lib.albums if opts.album else lib.items
            for obj in method(args):
                self._process(obj, write=ui.should_write())

        lastgenre_cmd.func = lastgenre_func
        return [lastgenre_cmd]

    def imported(
        self, session: library.Session, task: library.ImportTask
    ) -> None:
        """Import pipeline hook."""
        self._process(task.album if task.is_album else task.item, write=False)

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


"""Gets genres for imported music based on Last.fm tags.

Uses a provided whitelist file to determine which tags are valid genres.
The included (default) genre list was originally produced by scraping Wikipedia
and has been edited to remove some questionable entries.
The scraper script used is available here:
https://gist.github.com/1241307
"""

import codecs
import configparser
import os
import re
import traceback
from collections import Counter, defaultdict
from typing import Union

import pylast
import yaml

from beets import config, library, plugins, ui
from beets.library import Album, Item
from beets.ui import UserError
from beets.util import normpath, plurality, unique_list

LASTFM = pylast.LastFMNetwork(api_key=plugins.LASTFM_KEY)

PYLAST_EXCEPTIONS = (
    pylast.WSError,
    pylast.MalformedResponseError,
    pylast.NetworkError,
)

REPLACE = {
    "\u2010": "-",
}


# Canonicalization tree processing.


def flatten_tree(elem, path, branches):
    """Flatten nested lists/dictionaries into lists of strings
    (branches).
    """
    if not path:
        path = []

    if isinstance(elem, dict):
        for k, v in elem.items():
            flatten_tree(v, path + [k], branches)
    elif isinstance(elem, list):
        for sub in elem:
            flatten_tree(sub, path, branches)
    else:
        branches.append(path + [str(elem)])


def find_parents(candidate, branches):
    """Find parents genre of a given genre, ordered from the closest to
    the further parent.
    """
    for branch in branches:
        try:
            idx = branch.index(candidate.lower())
            return list(reversed(branch[: idx + 1]))
        except ValueError:
            continue
    return [candidate]


# Main plugin logic.

WHITELIST = os.path.join(os.path.dirname(__file__), "genres.txt")
C14N_TREE = os.path.join(os.path.dirname(__file__), "genres-tree.yaml")
ALIASES = os.path.join(os.path.dirname(__file__), "aliases.ini")


class LastGenrePlugin(plugins.BeetsPlugin):
    def __init__(self):
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
                "extended_debug": False,
                "blacklist": False,
                "aliases": True,  # True for default aliases file
            }
        )
        self.setup()

    def setup(self):
        """Setup plugin from config options"""
        if self.config["auto"]:
            self.import_stages = [self.imported]

        self._genre_cache = {}
        self.whitelist = self._load_whitelist()
        self.c14n_branches, self.canonicalize = self._load_c14n_tree()
        self.blacklist = self._load_blacklist()
        self.aliases = self._load_aliases()

    def _load_whitelist(self):
        whitelist = set()
        wl_filename = self.config["whitelist"].get()
        if wl_filename in (True, ""):  # Indicates the default whitelist.
            wl_filename = WHITELIST
        if wl_filename:
            wl_filename = normpath(wl_filename)
            with open(wl_filename, "rb") as f:
                for line in f:
                    line = line.decode("utf-8").strip().lower()
                    if line and not line.startswith("#"):
                        whitelist.add(line)
        return whitelist

    def _load_c14n_tree(self):
        c14n_branches = []
        c14n_filename = self.config["canonical"].get()
        canonicalize = c14n_filename is not False
        # Default tree
        if c14n_filename in (True, ""):
            c14n_filename = C14N_TREE
        elif not canonicalize and self.config["prefer_specific"].get():
            # prefer_specific requires a tree, load default tree
            c14n_filename = C14N_TREE
        # Read the tree
        if c14n_filename:
            self._log.debug("Loading canonicalization tree {0}", c14n_filename)
            c14n_filename = normpath(c14n_filename)
            with codecs.open(c14n_filename, "r", encoding="utf-8") as f:
                genres_tree = yaml.safe_load(f)
            flatten_tree(genres_tree, [], c14n_branches)
        return c14n_branches, canonicalize

    def _load_blacklist(self):
        """Load the blacklist from a configured file path.

        For maximum compatibility with regex patterns, a custom format is used:
        - Each section starts with an artist name, followed by a colon.
        - Subsequent lines are indented (at least one space, typically 4 spaces) and
          contain a regex pattern to match a genre.


        Supports a special '*' key in the blacklist for
        global forbidden genres.

        Example blacklist file format:
            Artist Name:
                pop
                rock
            Another Artist Name:
                jazz
            *:
                spoken word
                comedy

        Raises:
            UserError: if the file format is invalid.
        """
        blacklist = defaultdict(list)
        if not (bl_filename := self.config["blacklist"].get()):
            return blacklist
        if not os.path.isfile(bl_filename := normpath(bl_filename)):
            self._log.error("Blacklist file not found: {} .", bl_filename)
            return blacklist

        self._log.debug("Loading blacklist file {0}", bl_filename)
        section = None
        with open(bl_filename, "rb") as f:
            for lineno, line in enumerate(f, 1):
                line = line.decode("utf-8").lower()
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
                    # Pattern line: must be indented (at least one space)
                    if section is None:
                        raise UserError(
                            f"Blacklist regex pattern line before any section header "
                            f"at line {lineno}: {line}"
                        )
                    blacklist[section].append(line.strip())
        if self.config["extended_debug"]:
            self._log.debug("Blacklist: {}", blacklist)

        # Compile regex patterns
        compiled_blacklist = defaultdict(list)
        for artist, patterns in blacklist.items():
            compiled_patterns = []
            for pattern in patterns:
                try:
                    # Try to compile as regex first
                    compiled_patterns.append(re.compile(pattern, re.IGNORECASE))
                except re.error:
                    # If it fails, escape it and treat as literal string
                    escaped_pattern = re.escape(pattern)
                    compiled_patterns.append(
                        re.compile(escaped_pattern, re.IGNORECASE)
                    )
            compiled_blacklist[artist] = compiled_patterns
        return compiled_blacklist

    def _load_aliases(self):
        """Load genre aliases from a configured file path or the default aliases file."""
        aliases = []
        aliases_filename = self.config["aliases"].get()
        if aliases_filename in (
            True,
            "",
        ):  # Indicates the default aliases file.
            aliases_filename = ALIASES
        if aliases_filename:
            self._log.debug("Loading genre aliases file {0}", aliases_filename)
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
            except Exception as exc:
                self._log.error("Error loading aliases file: {0}", exc)
        return aliases

    @property
    def sources(self) -> tuple[str, ...]:
        """A tuple of allowed genre sources. May contain 'track',
        'album', or 'artist.'
        """
        source = self.config["source"].as_choice(("track", "album", "artist"))
        if source == "track":
            return "track", "album", "artist"
        if source == "album":
            return "album", "artist"
        if source == "artist":
            return ("artist",)
        return tuple()

    # More canonicalization and general helpers.

    def _get_depth(self, tag):
        """Find the depth of a tag in the genres tree."""
        depth = None
        for key, value in enumerate(self.c14n_branches):
            if tag in value:
                depth = value.index(tag)
                break
        return depth

    def _sort_by_depth(self, tags):
        """Given a list of tags, sort the tags by their depths in the
        genre tree.
        """
        depth_tag_pairs = [(self._get_depth(t), t) for t in tags]
        depth_tag_pairs = [e for e in depth_tag_pairs if e[0] is not None]
        depth_tag_pairs.sort(reverse=True)
        return [p[1] for p in depth_tag_pairs]

    def _resolve_genres(self, tags: list[str]) -> list[str]:
        """Filter, deduplicate, sort, canonicalize provided genres list.

        - Returns an empty list if the input tags list is empty.
        - If canonicalization is enabled, it extends the list by incorporating
          parent genres from the canonicalization tree. When a whitelist is set,
          only parent tags that pass a validity check (_is_valid) are included;
          otherwise, it adds the oldest ancestor.
        - During canonicalization, it stops adding parent tags if the count of
          tags reaches the configured limit (count).
        - The tags list is then deduplicated to ensure only unique genres are
          retained.
        - Optionally, if the 'prefer_specific' configuration is enabled, the
          list is sorted by the specificity (depth in the canonicalization tree)
          of the genres.
        - The method then filters the tag list, ensuring that only valid
          genres (those that pass the _is_valid method) are kept. If a
          whitelist is set, only genres in the whitelist are considered valid
          (which may even result in no genres at all being retained).
        - Finally, the filtered list of genres, limited to
          the configured count is returned.
        """
        if not tags:
            return []

        count = self.config["count"].get(int)
        if self.canonicalize:
            # Extend the list to consider tags parents in the c14n tree
            tags_all = []
            for tag in tags:
                # Add parents that are in the whitelist, or add the oldest
                # ancestor if no whitelist
                if self.whitelist:
                    parents = [
                        x
                        for x in find_parents(tag, self.c14n_branches)
                        if self._is_valid(x)
                    ]
                else:
                    parents = [find_parents(tag, self.c14n_branches)[-1]]

                tags_all += parents
                # Stop if we have enough tags already, unless we need to find
                # the most specific tag (instead of the most popular).
                if (
                    not self.config["prefer_specific"]
                    and len(tags_all) >= count
                ):
                    break
            tags = tags_all

        tags = unique_list(tags)

        # Sort the tags by specificity.
        if self.config["prefer_specific"]:
            tags = self._sort_by_depth(tags)

        # c14n only adds allowed genres but we may have had forbidden genres in
        # the original tags list
        valid_tags = self._filter_valid_genres(tags)
        return valid_tags[: self.config["count"].get(int)]

    def fetch_genre(self, lastfm_obj):
        """Return the genre for a pylast entity or None if no suitable genre
        can be found. Ex. 'Electronic, House, Dance'
        """
        min_weight = self.config["min_weight"].get(int)
        return self._tags_for(lastfm_obj, min_weight)

    def _filter_valid_genres(
        self, genres: list[str], artist: str = ""
    ) -> list[str]:
        """Filter list of genres, only keep valid and not forbidden."""
        if not genres:
            return []
        # Apply aliases before filtering
        genres = self._apply_aliases(genres)
        return [
            x
            for x in genres
            if self._is_valid(x) and not self._is_forbidden(x, artist)
        ]

    def _is_valid(self, genre: str) -> bool:
        """Check if the genre is valid.

        Depending on the whitelist property, valid means a genre is in the
        whitelist or any genre is allowed.
        """
        if genre and (not self.whitelist or genre.lower() in self.whitelist):
            return True
        return False

    def _is_forbidden(self, genre: str, artist: str) -> bool:
        """Return True if the genre is on the blacklist for the artist.

        See `_load_blacklist` docstring for the blacklist file format.
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

    def _apply_aliases(self, genres):
        """Apply regex aliases to the genre list.

        For each genre, apply only the first matching alias pattern.
        """
        if not self.aliases or not genres:
            return genres

        result = []
        for genre in genres:
            replaced = genre
            for alias_pair in self.aliases:
                for pattern, replacement in alias_pair.items():
                    try:
                        new_genre = re.sub(
                            pattern, replacement, replaced, flags=re.IGNORECASE
                        )
                        if new_genre != replaced:
                            replaced = new_genre
                            break  # Stop after first match
                    except re.error as exc:
                        self._log.error(
                            "Regex error in pattern '{0}': {1}", pattern, exc
                        )
                else:
                    continue
                break  # Stop after first alias_pair match
            result.append(replaced)
        if self.config["extended_debug"]:
            self._log.debug("Genres after applying aliases: {0}", result)
        return result

    # Cached last.fm entity lookups.

    def _last_lookup(self, entity, method, *args):
        """Get a genre based on the named entity using the callable `method`
        whose arguments are given in the sequence `args`. The genre lookup
        is cached based on the entity name and the arguments.

        Before the lookup, each argument has the "-" Unicode character replaced
        with its rough ASCII equivalents in order to return better results from
        the Last.fm database.
        """
        # Shortcut if we're missing metadata.
        if any(not s for s in args):
            return None

        key = f"{entity}.{'-'.join(str(a) for a in args)}"
        if key not in self._genre_cache:
            args = [a.replace("\u2010", "-") for a in args]
            self._genre_cache[key] = self.fetch_genre(method(*args))

        genre = self._genre_cache[key]
        if self.config["extended_debug"]:
            self._log.debug(f"last.fm (unfiltered) {entity} tags: {genre}")
        return genre

    def fetch_album_genre(self, obj):
        """Return the album genre for this Item or Album."""
        return self._filter_valid_genres(
            self._last_lookup(
                "album", LASTFM.get_album, obj.albumartist, obj.album
            ),
            artist=obj.albumartist,
        )

    def fetch_album_artist_genre(self, obj):
        """Return the album artist genre for this Item or Album."""
        return self._filter_valid_genres(
            self._last_lookup("artist", LASTFM.get_artist, obj.albumartist),
            artist=obj.albumartist,
        )

    def fetch_split_album_artist_genre(self, split_artist):
        """Return the artist genre for any passed artist name.

        Used for multi-artist albums where the artist name may not match
        the album artist exactly and a split by separator is needed to get a last.fm
        result.
        """
        return self._filter_valid_genres(
            self._last_lookup("artist", LASTFM.get_artist, split_artist),
            artist=split_artist,
        )

    def fetch_artist_genre(self, item):
        """Returns the track artist genre for this Item."""
        return self._filter_valid_genres(
            self._last_lookup("artist", LASTFM.get_artist, item.artist),
            artist=item.artist,
        )

    def fetch_track_genre(self, obj):
        """Returns the track genre for this Item."""
        return self._filter_valid_genres(
            self._last_lookup("track", LASTFM.get_track, obj.artist, obj.title),
            artist=obj.artist,
        )

    # Main processing: _get_genre() and helpers.

    def _format_and_stringify(self, tags: list[str]) -> str:
        """Format to title_case if configured and return as delimited string."""
        if self.config["title_case"]:
            formatted = [tag.title() for tag in tags]
        else:
            formatted = tags

        return self.config["separator"].as_str().join(formatted)

    def _get_existing_genres(self, obj: Union[Album, Item]) -> list[str]:
        """Return a list of genres for this Item or Album. Empty string genres
        are removed."""
        separator = self.config["separator"].get()
        if isinstance(obj, library.Item):
            genre_string = obj.get("genre", with_album=False)
        else:
            genre_string = obj.get("genre")

        # Check if any separators are present before attempting to split
        if separator in genre_string:
            item_genre = genre_string.split(separator)
        else:
            # Check for alternative separators
            split_separator = None
            # Intentionally keep whitespace (trim later)
            for alt_sep in [";", "/", ","]:
                if alt_sep in genre_string:
                    split_separator = alt_sep
                    break

            if split_separator:
                item_genre = genre_string.split(split_separator)
            else:
                # No separators found, return an empty or single genre list
                item_genre = [genre_string] if genre_string else []

        # Filter out empty strings and strip whitespace
        final_keep = [g.strip() for g in item_genre if g.strip()]
        self._log.debug(
            f"Existing genres gathered: {final_keep}"
        )
        return final_keep

    def _combine_resolve_and_log(
        self, old: list[str], new: list[str]
    ) -> list[str]:
        """Combine old and new genres and process via _resolve_genres."""
        self._log.debug(f"valid last.fm tags: {new}")
        self._log.debug(f"existing genres taken into account: {old}")
        combined = old + new
        return self._resolve_genres(combined)

    def _get_genre(
        self, obj: Union[Album, Item]
    ) -> tuple[Union[str, None], ...]:
        """Get the final genre string for an Album or Item object.

        `self.sources` specifies allowed genre sources. Starting with the first
        source in this tuple, the following stages run through until a genre is
        found or no options are left:
            - track (for Items only)
            - album
            - artist, albumartist or "most popular track genre" (for VA-albums)
            - original fallback
            - configured fallback
            - None

        A `(genre, label)` pair is returned, where `label` is a string used for
        logging. For example, "keep + artist, whitelist" indicates that existing
        genres were combined with new last.fm genres and whitelist filtering was
        applied, while "artist, any" means only new last.fm genres are included
        and the whitelist feature was disabled.
        """
        keep_genres = []
        new_genres = []
        label = ""
        genres = self._get_existing_genres(obj)

        if genres and not self.config["force"]:
            # Without force pre-populated tags are returned as-is.
            label = "keep any, no-force"
            if isinstance(obj, library.Item):
                return obj.get("genre", with_album=False), label
            return obj.get("genre"), label

        if self.config["force"]:
            # Force doesn't keep any unless keep_existing is set.
            # Whitelist validation is handled in _resolve_genres.
            if self.config["keep_existing"]:
                keep_genres = [g.lower() for g in genres]

        # Run through stages: track, album, artist,
        # album artist, or most popular track genre.
        if isinstance(obj, library.Item) and "track" in self.sources:
            if new_genres := self.fetch_track_genre(obj):
                label = "track"

        if not new_genres and "album" in self.sources:
            if new_genres := self.fetch_album_genre(obj):
                label = "album"

        if not new_genres and "artist" in self.sources:
            new_genres = []
            if isinstance(obj, library.Item):
                new_genres = self.fetch_artist_genre(obj)
                label = "artist"
            elif obj.albumartist != config["va_name"].as_str():
                new_genres = self.fetch_album_artist_genre(obj)
                label = "album artist"
                if not new_genres:
                    if self.config["extended_debug"]:
                        self._log.debug(
                            'No album artist genre found for "{0.albumartist}"',
                            obj,
                        )
                    separators = [
                        re.escape(self.config["separator"].get()),
                        " feat\\. ",
                        " featuring ",
                        " & ",
                        " vs\\. ",
                        " x ",
                        " / ",
                        " + ",
                        " and ",
                        " \\| ",
                    ]
                    if any(
                        re.sub(r"\\", "", sep) in obj.albumartist
                        for sep in separators
                    ):
                        if self.config["extended_debug"]:
                            self._log.debug(
                                "Found separators in album artist - splitting..."
                            )
                        # Split on all separators using regex
                        pattern = "|".join(separators)
                        albumartists = re.split(pattern, obj.albumartist)
                        for albumartist in albumartists:
                            albumartist = albumartist.strip()
                            if self.config["extended_debug"]:
                                self._log.debug(
                                    'Fetching multi-artist album genre for "{0}"',
                                    albumartist,
                                )
                            new_genres += self.fetch_split_album_artist_genre(
                                albumartist
                            )
                            if new_genres:
                                label = "album artist (split)"
            else:
                # For "Various Artists", pick the most popular track genre.
                item_genres = []
                for item in obj.items():
                    item_genre = None
                    if "track" in self.sources:
                        item_genre = self.fetch_track_genre(item)
                    if not item_genre:
                        item_genre = self.fetch_artist_genre(item)
                    if item_genre:
                        item_genres += item_genre
                if item_genres:
                    # Get a ranked list of genres by popularity
                    genre_counts = Counter(item_genres)
                    top_n = 2  # Change this to your desired number
                    most_popular_genres = [
                        g for g, _ in genre_counts.most_common(top_n)
                    ]
                    new_genres = most_popular_genres
                    label = f"most popular {top_n} track genres"
                    self._log.debug(
                        'Most popular track genres {} for VA album.',
                        most_popular_genres,
                    )

        # Return with a combined or freshly fetched genre list.
        if new_genres:
            resolved_genres = self._combine_resolve_and_log(
                keep_genres, new_genres
            )
            if resolved_genres:
                suffix = "whitelist" if self.whitelist else "any"
                label += f", {suffix}"
                if keep_genres:
                    label = f"keep + {label}"
                return self._format_and_stringify(resolved_genres), label

        # Nothing found, leave original (split up) genres if configured and valid.
        if keep_genres and self.config["keep_existing"]:
            valid_keep = self._filter_valid_genres(keep_genres)
            resolved_keep = self._resolve_genres(valid_keep)
            self._log.debug("Resolved keep genres: {}", resolved_keep)
            return self._format_and_stringify(
                resolved_keep
            ), "original fallback (split and resolved)"

        # Nothing found, leave original (single) genre if configured and valid.
        if obj.genre and self.config["keep_existing"]:
            if not self.whitelist or self._is_valid(obj.genre.lower()):
                return obj.genre, "original fallback (as-is)"

        self._log.debug("Pretend: No genre found. obj.genre is: {}", obj.genre)
        # Return fallback string.
        if fallback := self.config["fallback"].get():
            return fallback, "fallback"

        # No fallback configured.
        return None, "fallback unconfigured"

    # Beets plugin hooks and CLI.

    def commands(self):
        lastgenre_cmd = ui.Subcommand("lastgenre", help="fetch genres")
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
            help="combine with existing genres when modifying",
        )
        lastgenre_cmd.parser.add_option(
            "-K",
            "--no-keep-existing",
            dest="keep_existing",
            action="store_false",
            help="don't combine with existing genres when modifying",
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
        lastgenre_cmd.parser.add_option(
            "-d",
            "--debug",
            action="store_true",
            dest="extended_debug",
            help="extended last.fm debug logging",
        )
        lastgenre_cmd.parser.add_option(
            "-p",
            "--pretend",
            action="store_true",
            dest="pretend",
            help="print what would be assigned, do not modify genres",
        )
        lastgenre_cmd.parser.set_defaults(album=True)

        def lastgenre_func(lib, opts, args):
            write = ui.should_write()
            self.config.set_args(opts)
            if opts.pretend:
                self.config["force"].set(True)

            if opts.album:
                # Fetch genres for whole albums
                for album in lib.albums(args):
                    album_genre, label = self._get_genre(album)
                    if opts.pretend:
                        self._log.info(
                            'Pretend: album "{0.album}" would get genre '
                            "({1}): {2}",
                            album,
                            label,
                            album_genre,
                        )
                    else:
                        self._log.info(
                            'genre for album "{0.album}" ({1}): {2}',
                            album,
                            label,
                            album_genre,
                        )
                        album.genre = album_genre
                        if "track" in self.sources:
                            album.store(inherit=False)
                        else:
                            album.store()

                    for item in album.items():
                        # If we're using track-level sources, also look up each
                        # track on the album.
                        if "track" in self.sources:
                            item_genre, label = self._get_genre(item)

                            # Decide if we should fall back to the album genre
                            if (
                                not item_genre
                                or item_genre == self.config["fallback"].get()
                                and album_genre != self.config["fallback"].get()
                            ):
                                item.genre = album_genre
                                label = "album genre fallback"
                            else:
                                item.genre = item_genre

                            if opts.pretend:
                                self._log.info(
                                    'Pretend: track "{0.title}" would get genre '
                                    '({1}): {2}',
                                    item,
                                    label,
                                    item.genre,
                                )
                            else:
                                item.store()
                                if item.genre:
                                    self._log.info(
                                        'genre for track "{0.title}" ({1}): {2}',
                                        item,
                                        label,
                                        item.genre,
                                    )
                                else:
                                    self._log.info(
                                        'No genre found for track "{0.title}"',
                                        item,
                                    )
                                if write:
                                    item.try_write()
            else:
                # Just query single tracks or singletons
                for item in lib.items(args):
                    singleton_genre, label = self._get_genre(item)
                    if opts.pretend:
                        self._log.info(
                            'Pretend: track "{0.title}" would get genre: '
                            '({1}) {2}',
                            item,
                            label,
                            singleton_genre,
                        )
                    else:
                        item.genre = singleton_genre
                        item.store()
                        self._log.info(
                            "genre for track {0.title} ({1}): {2}",
                            item,
                            label,
                            singleton_genre,
                        )

        lastgenre_cmd.func = lastgenre_func
        return [lastgenre_cmd]

    def imported(self, session, task):
        """Event hook called when an import task finishes."""
        if task.is_album:
            album = task.album
            album.genre, src = self._get_genre(album)
            self._log.debug(
                'genre for album "{0.album}" ({1}): {0.genre}', album, src
            )

            # If we're using track-level sources, store the album genre only,
            # then also look up individual track genres.
            if "track" in self.sources:
                album.store(inherit=False)
                for item in album.items():
                    item.genre, src = self._get_genre(item)
                    self._log.debug(
                        'genre for track "{0.title}" ({1}): {0.genre}',
                        item,
                        src,
                    )
                    item.store()
            # Store the album genre and inherit to tracks.
            else:
                album.store()

        else:
            item = task.item
            item.genre, src = self._get_genre(item)
            self._log.debug(
                'genre for track "{0.title}" ({1}): {0.genre}',
                item,
                src,
            )
            item.store()

    def _tags_for(self, obj, min_weight=None):
        """Core genre identification routine.

        Given a pylast entity (album or track), return a list of
        tag names for that entity. Return an empty list if the entity is
        not found or another error occurs.

        If `min_weight` is specified, tags are filtered by weight.
        """
        # Work around an inconsistency in pylast where
        # Album.get_top_tags() does not return TopItem instances.
        # https://github.com/pylast/pylast/issues/86
        if isinstance(obj, pylast.Album):
            obj = super(pylast.Album, obj)

        try:
            res = obj.get_top_tags()
        except PYLAST_EXCEPTIONS as exc:
            self._log.debug("last.fm error: {0}", exc)
            return []
        except Exception as exc:
            # Isolate bugs in pylast.
            self._log.debug("{}", traceback.format_exc())
            self._log.error("error in pylast library: {0}", exc)
            return []

        # Filter by weight (optionally).
        if min_weight:
            res = [el for el in res if (int(el.weight or 0)) >= min_weight]

        # Get strings from tags.
        res = [el.item.get_name().lower() for el in res]

        return res

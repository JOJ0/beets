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

"""Canonicalization logic for the lastgenre plugin."""

from __future__ import annotations

from typing import Any


def flatten_tree(
    elem: dict[Any, Any] | list[Any] | str,
    path: list[str],
    branches: list[list[str]],
) -> None:
    """Flatten nested lists/dictionaries into lists of strings (branches)."""
    if not path:
        path = []

    if isinstance(elem, dict):
        for k, v in elem.items():
            flatten_tree(v, [*path, k], branches)
    elif isinstance(elem, list):
        for sub in elem:
            flatten_tree(sub, path, branches)
    else:
        branches.append([*path, str(elem)])


def find_parents(candidate: str, branches: list[list[str]]) -> list[str]:
    """Find parent genres of a given genre.

    Returns genres ordered from the candidate to the furthest parent.
    """
    for branch in branches:
        try:
            idx = branch.index(candidate.lower())
            return list(reversed(branch[: idx + 1]))
        except ValueError:
            continue
    return [candidate]


class GenreCanonicalizer:
    """Handles genre canonicalization using a tree structure."""

    def __init__(self, c14n_branches: list[list[str]]):
        """Initialize canonicalizer with tree branches."""
        self.c14n_branches = c14n_branches

    def find_parents(self, genre: str) -> list[str]:
        """Find parent genres for the given genre."""
        return find_parents(genre, self.c14n_branches)

    def get_depth(self, genre: str) -> int:
        """Get the depth of a genre in the tree.

        The depth is determined by the maximum number of ancestors (including
        the genre itself) across all branches where the genre appears.
        """
        depths = []
        for branch in self.c14n_branches:
            try:
                idx = branch.index(genre.lower())
                depths.append(idx + 1)
            except ValueError:
                continue
        return max(depths) if depths else 0

    def sort_by_depth(self, genres: list[str]) -> list[str]:
        """Sort genres by their depth in the canonicalization tree.

        More specific (deeper) genres come first.
        """
        return sorted(genres, key=self.get_depth, reverse=True)

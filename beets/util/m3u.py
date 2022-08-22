# This file is part of beets.
# Copyright 2022, J0J0 Todos.
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

"""Provides utilities to read, write an manipulate m3u playlist files.
"""


from beets.util import syspath


class EmptyPlaylistError(Exception):
    """An error that should be raised when a playlist file without media files
    is saved or loaded.
    """
    pass


class M3UFile():
    def __init__(self, path):
        """Reads and writes m3u or m3u8 playlist files.

        ``path`` is the full path to the playlist file.

        The playlist file type, m3u or m3u8 is determined by 1) the ending
        being m3u8 and 2) the file paths contained in the list being utf-8
        encoded. Since the list is passed from the outside, this is currently
        out of control of this class.
        """
        self.path = path
        self.extm3u = False
        self.media_list = []

    def load(self):
        """Reads the m3u file from disk and sets the object's attributes.
        """
        with open(syspath(self.path), "r") as playlist_file:
            raw_contents = playlist_file.readlines()
        self.extm3u = True if raw_contents[0] == "#EXTM3U\n" else False
        for line in raw_contents[1:]:
            if line.startswith("#"):
                # Some EXTM3U comment, do something. FIXME
                continue
            self.media_list.append(line)
        if not self.media_list:
            raise EmptyPlaylistError

    def set_contents(self, media_list, extm3u=True):
        """Sets self.media_list to a list of media file paths,

        and sets additional flags, changing the final m3u-file's format.

        ``media_list`` is a list of paths to media files that should be added
        to the playlist (relative or absolute paths, that's the responsibility
        of the caller). By default the ``extm3u`` flag is set, to ensure a
        save-operation writes an m3u-extended playlist (comment "#EXTM3U" at
        the top of the file).
        """
        self.media_list = media_list
        self.extm3u = extm3u

    def write(self):
        """Writes the m3u file to disk."""
        header = ["#EXTM3U"] if self.extm3u else []
        if not self.media_list:
            raise EmptyPlaylistError
        contents = header + self.media_list
        with open(syspath(self.path), "w") as playlist_file:
            playlist_file.writelines('\n'.join(contents))
            playlist_file.write('\n')  # Final linefeed to prevent noeol file.

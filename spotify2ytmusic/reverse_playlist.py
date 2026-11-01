#!/usr/bin/env python3

import json
import os
import shutil
from argparse import ArgumentParser


def reverse_playlist(input_file="playlists.json", verbose=True, replace=False) -> int:
    if not os.path.exists(input_file):
        if verbose:
            print(f"Input file {input_file!r} does not exist, exiting...")
        return 1

    root, ext = os.path.splitext(input_file)
    backup_file = f"{root}_backup{ext or '.json'}"
    if os.path.exists(backup_file) and not replace:
        if verbose:
            print(
                f"Backup file {backup_file!r} already exists and no replace argument detected, exiting..."
            )
        return 1

    print(f"Backing up file to {backup_file}...")
    shutil.copyfile(input_file, backup_file)
    # Load the JSON file
    with open(input_file, "r", encoding="utf-8") as file:
        if verbose:
            print("Loading initial JSON file...")
        data = json.load(file)

    # Copy the data to a new dictionary
    data2 = data.copy()

    if verbose:
        print("Reversing playlists...")
    # Reverse the order of items in the "tracks" list
    for i in range(len(data2["playlists"])):
        # Reverse the tracks in the playlist
        data2["playlists"][i]["tracks"] = data2["playlists"][i]["tracks"][::-1]

    if verbose:
        print("Writing to file... (this can take a while)")
    # Write the modified JSON back to the file
    with open(input_file, "w", encoding="utf-8") as file:
        json.dump(data2, file, ensure_ascii=False)

    if verbose:
        print("Done!")
        print(f"File can be found at {input_file}")

    return 0


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("input_file", type=str, help="Path to the input file")
    parser.add_argument(
        "-v", "--verbose", action="store_false", help="Enable verbose mode"
    )
    parser.add_argument(
        "-r",
        "--replace",
        action="store_true",
        help="Replace the output file if already existing",
    )

    args = parser.parse_args()

    reverse_playlist(args.input_file, args.verbose, args.replace)

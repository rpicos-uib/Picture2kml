# Picture2kml

Reads the GPS coordinates stored in the EXIF data of the pictures in a directory
and creates a KML file with one placemark per picture, which you can open in
Google Earth, Google My Maps, QGIS and similar tools.

Each placemark has the file name, the coordinates, and, when the picture records
them, the altitude and capture time. Placemarks are in chronological order.
Because capture times are written as KML timestamps, Google Earth's time slider
works on the result.

## Installation

Requires Python 3.9 or newer.

```bash
pip install -r requirements.txt
```

This installs Pillow, which handles JPEG, PNG, TIFF and WebP. It also installs
`pillow-heif`, which is only needed for iPhone HEIC/HEIF pictures: without it the
script still runs and reports those files as skipped.

## Usage

```bash
python picture2kml.py ~/Pictures/holiday
```

This writes `~/Pictures/holiday/photos.kml` and prints a summary:

```
132 pictures: 118 geotagged, 13 without GPS data, 1 unreadable
wrote /home/you/Pictures/holiday/photos.kml
```

| Option | Meaning |
| --- | --- |
| `-o`, `--output FILE` | KML file to write (default: `photos.kml` inside the directory) |
| `-r`, `--recursive` | Also search subdirectories |
| `-t`, `--title TEXT` | Document title (default: the directory name) |

The exit status is 0 on success, 1 if no geotagged pictures were found (no file
is written), and 2 if the directory does not exist.

Pictures are skipped rather than placed on the map if their GPS tags are unusable:
no hemisphere reference, out-of-range values, or `0,0`, which cameras commonly
write when they have no satellite fix.

## Privacy

A KML file made from your pictures reveals where you were and when. The included
`.gitignore` keeps `*.kml` files and pictures out of version control.

## Tests

```bash
python -m unittest
```

## License

[MIT](LICENSE)

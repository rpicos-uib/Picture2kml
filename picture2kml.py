#!/usr/bin/env python3
"""Create a KML file from the GPS coordinates stored in a directory of pictures."""

import argparse
import html
import math
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from PIL import ExifTags, Image

try:
    from pillow_heif import register_heif_opener
except ImportError:
    HEIF_SUPPORTED = False
else:
    register_heif_opener()
    HEIF_SUPPORTED = True

KML_NS = "http://www.opengis.net/kml/2.2"
HEIF_EXTENSIONS = {".heic", ".heif"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"} | HEIF_EXTENSIONS

TAG_DATETIME = 0x0132
TAG_DATETIME_ORIGINAL = 0x9003
TAG_OFFSET_TIME_ORIGINAL = 0x9011
GPS_LATITUDE_REF = 1
GPS_LATITUDE = 2
GPS_LONGITUDE_REF = 3
GPS_LONGITUDE = 4
GPS_ALTITUDE_REF = 5
GPS_ALTITUDE = 6
PATH_COLOR = "ff0055ff"  # KML colors are aabbggrr: opaque orange
PATH_WIDTH = "3"

# Only metadata is read, so the size limit against huge decoded images does not
# apply, and it would otherwise reject large panoramas.
Image.MAX_IMAGE_PIXELS = None


@dataclass
class Photo:
    path: Path
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    taken: Optional[str] = None


def clean_text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    return str(value).strip("\x00 ")


def to_degrees(dms, ref) -> Optional[float]:
    """Convert EXIF (degrees, minutes, seconds) and a hemisphere ref to decimal degrees."""
    ref = clean_text(ref).upper()
    if ref not in ("N", "S", "E", "W"):
        return None
    try:
        degrees = sum(float(value) / 60**i for i, value in enumerate(dms))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return -degrees if ref in ("S", "W") else degrees


def read_coordinates(gps) -> Optional[tuple]:
    """Return (latitude, longitude), or None if the GPS tags hold no usable fix."""
    latitude = to_degrees(gps.get(GPS_LATITUDE), gps.get(GPS_LATITUDE_REF, ""))
    longitude = to_degrees(gps.get(GPS_LONGITUDE), gps.get(GPS_LONGITUDE_REF, ""))
    if latitude is None or longitude is None:
        return None
    # The comparisons also reject NaN, which is what a 0/0 EXIF rational becomes.
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    # Cameras without a satellite fix commonly write 0,0.
    if latitude == 0 and longitude == 0:
        return None
    return latitude, longitude


def read_altitude(gps) -> Optional[float]:
    try:
        altitude = float(gps[GPS_ALTITUDE])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(altitude):
        return None
    below_sea_level = gps.get(GPS_ALTITUDE_REF) in (1, b"\x01")
    return -altitude if below_sea_level else altitude


def read_timestamp(exif) -> Optional[str]:
    """Return the capture time as an ISO 8601 string, with its UTC offset if known."""
    details = exif.get_ifd(ExifTags.IFD.Exif)
    raw = details.get(TAG_DATETIME_ORIGINAL) or exif.get(TAG_DATETIME)
    if not raw:
        return None
    try:
        taken = datetime.strptime(clean_text(raw), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    offset = clean_text(details.get(TAG_OFFSET_TIME_ORIGINAL, ""))
    if not re.fullmatch(r"[+-]\d\d:\d\d", offset):
        offset = ""
    return taken.isoformat() + offset


def read_photo(path: Path) -> Optional[Photo]:
    """Return the geotagged photo at path, or None if it has no usable GPS data."""
    with Image.open(path) as image:
        exif = image.getexif()
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        coordinates = read_coordinates(gps)
        if coordinates is None:
            return None
        return Photo(path, *coordinates, read_altitude(gps), read_timestamp(exif))


def find_images(directory: Path, recursive: bool) -> list:
    candidates = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        path
        for path in candidates
        if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file()
    )


def describe(photo: Photo, directory: Path) -> str:
    lines = []
    if photo.taken:
        lines.append(f"Taken: {photo.taken}")
    if photo.altitude is not None:
        lines.append(f"Altitude: {photo.altitude:.1f} m")
    lines.append(f"File: {html.escape(photo.path.relative_to(directory).as_posix())}")
    return "<br/>".join(lines)


def sort_key(photo: Photo) -> tuple:
    """Order by capture time, comparing UTC when the offset is known; undated go last."""
    if photo.taken is None:
        return (True, datetime.min, photo.path)
    taken = datetime.fromisoformat(photo.taken)
    if taken.tzinfo is not None:
        taken = taken.astimezone(timezone.utc).replace(tzinfo=None)
    return (False, taken, photo.path)


def format_coordinates(photo: Photo) -> str:
    coordinates = f"{photo.longitude:.7f},{photo.latitude:.7f}"
    if photo.altitude is not None:
        coordinates += f",{photo.altitude:.1f}"
    return coordinates


def add_path(document: ET.Element, photos: list) -> None:
    placemark = ET.SubElement(document, f"{{{KML_NS}}}Placemark")
    ET.SubElement(placemark, f"{{{KML_NS}}}name").text = "Path"
    ET.SubElement(placemark, f"{{{KML_NS}}}description").text = (
        f"Through {len(photos)} pictures in capture order"
    )
    style = ET.SubElement(placemark, f"{{{KML_NS}}}Style")
    line_style = ET.SubElement(style, f"{{{KML_NS}}}LineStyle")
    ET.SubElement(line_style, f"{{{KML_NS}}}color").text = PATH_COLOR
    ET.SubElement(line_style, f"{{{KML_NS}}}width").text = PATH_WIDTH
    line = ET.SubElement(placemark, f"{{{KML_NS}}}LineString")
    ET.SubElement(line, f"{{{KML_NS}}}tessellate").text = "1"
    ET.SubElement(line, f"{{{KML_NS}}}coordinates").text = " ".join(
        format_coordinates(photo) for photo in photos
    )


def build_kml(
    photos: list, directory: Path, title: str, path_photos: list = (), points: bool = True
) -> ET.ElementTree:
    ET.register_namespace("", KML_NS)
    kml = ET.Element(f"{{{KML_NS}}}kml")
    document = ET.SubElement(kml, f"{{{KML_NS}}}Document")
    ET.SubElement(document, f"{{{KML_NS}}}name").text = title

    if path_photos:
        add_path(document, path_photos)

    for photo in photos if points else ():
        placemark = ET.SubElement(document, f"{{{KML_NS}}}Placemark")
        ET.SubElement(placemark, f"{{{KML_NS}}}name").text = photo.path.name
        ET.SubElement(placemark, f"{{{KML_NS}}}description").text = describe(photo, directory)
        if photo.taken:
            timestamp = ET.SubElement(placemark, f"{{{KML_NS}}}TimeStamp")
            ET.SubElement(timestamp, f"{{{KML_NS}}}when").text = photo.taken
        point = ET.SubElement(placemark, f"{{{KML_NS}}}Point")
        ET.SubElement(point, f"{{{KML_NS}}}coordinates").text = format_coordinates(photo)

    tree = ET.ElementTree(kml)
    ET.indent(tree)
    return tree


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory containing the pictures")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="KML file to write (default: photos.kml inside the directory)",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="also search subdirectories"
    )
    parser.add_argument("-t", "--title", help="document title (default: the directory name)")
    path = parser.add_mutually_exclusive_group()
    path.add_argument(
        "-p",
        "--path",
        action="store_true",
        help="also draw a line through the pictures in capture order",
    )
    path.add_argument(
        "--path-only", action="store_true", help="draw the line without the placemarks"
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    directory = args.directory.resolve()
    if not directory.is_dir():
        print(f"error: {args.directory} is not a directory", file=sys.stderr)
        return 2

    images = find_images(directory, args.recursive)
    photos = []
    no_gps = 0
    unreadable = []
    for path in images:
        if path.suffix.lower() in HEIF_EXTENSIONS and not HEIF_SUPPORTED:
            unreadable.append((path, "HEIC/HEIF support needs: pip install pillow-heif"))
            continue
        try:
            photo = read_photo(path)
        except (OSError, SyntaxError, ValueError) as error:
            unreadable.append((path, error))
            continue
        if photo is None:
            no_gps += 1
        else:
            photos.append(photo)

    for path, reason in unreadable:
        print(f"warning: skipped {path.name}: {reason}", file=sys.stderr)
    print(
        f"{len(images)} pictures: {len(photos)} geotagged, "
        f"{no_gps} without GPS data, {len(unreadable)} unreadable"
    )

    if not photos:
        print("error: no geotagged pictures found, no KML written", file=sys.stderr)
        return 1

    photos.sort(key=sort_key)

    path_photos = []
    if args.path or args.path_only:
        # Undated pictures have no known position in the sequence.
        path_photos = [photo for photo in photos if photo.taken]
        if len(path_photos) < 2:
            print("error: a path needs at least 2 pictures with a date", file=sys.stderr)
            return 1
        if len(path_photos) < len(photos):
            print(f"{len(photos) - len(path_photos)} undated pictures left out of the path")

    output = args.output or directory / "photos.kml"
    tree = build_kml(
        photos, directory, args.title or directory.name, path_photos, not args.path_only
    )
    tree.write(output, encoding="UTF-8", xml_declaration=True)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

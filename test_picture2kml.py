import contextlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import ExifTags, Image, TiffImagePlugin

import picture2kml

NS = {"kml": picture2kml.KML_NS}


def rational(value):
    return TiffImagePlugin.IFDRational(value)


def save_picture(path, latitude=None, longitude=None, altitude=None, taken=None, offset=None):
    """Write a small picture with the given (degrees, minutes, seconds) GPS tags."""
    exif = Image.Exif()
    if taken:
        details = exif.get_ifd(ExifTags.IFD.Exif)
        details[picture2kml.TAG_DATETIME_ORIGINAL] = taken
        if offset:
            details[picture2kml.TAG_OFFSET_TIME_ORIGINAL] = offset
    if latitude:
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps[picture2kml.GPS_LATITUDE_REF], lat = latitude
        gps[picture2kml.GPS_LATITUDE] = tuple(rational(v) for v in lat)
        gps[picture2kml.GPS_LONGITUDE_REF], lon = longitude
        gps[picture2kml.GPS_LONGITUDE] = tuple(rational(v) for v in lon)
        if altitude is not None:
            gps[picture2kml.GPS_ALTITUDE_REF] = 1 if altitude < 0 else 0
            gps[picture2kml.GPS_ALTITUDE] = rational(abs(altitude))
    Image.new("RGB", (8, 8), "red").save(path, exif=exif)


class ToDegreesTest(unittest.TestCase):
    def test_hemispheres(self):
        self.assertAlmostEqual(picture2kml.to_degrees((39, 30, 0), "N"), 39.5)
        self.assertAlmostEqual(picture2kml.to_degrees((39, 30, 0), "S"), -39.5)
        self.assertAlmostEqual(picture2kml.to_degrees((2, 45, 36), b"W\x00"), -2.76)

    def test_missing_hemisphere_is_not_guessed(self):
        self.assertIsNone(picture2kml.to_degrees((39, 30, 0), ""))

    def test_missing_values(self):
        self.assertIsNone(picture2kml.to_degrees(None, "N"))


class ReadCoordinatesTest(unittest.TestCase):
    def gps(self, latitude, longitude):
        return {
            picture2kml.GPS_LATITUDE_REF: "N",
            picture2kml.GPS_LATITUDE: latitude,
            picture2kml.GPS_LONGITUDE_REF: "E",
            picture2kml.GPS_LONGITUDE: longitude,
        }

    def test_no_fix_written_as_zero(self):
        self.assertIsNone(picture2kml.read_coordinates(self.gps((0, 0, 0), (0, 0, 0))))

    def test_undefined_rational(self):
        undefined = TiffImagePlugin.IFDRational(0, 0)
        self.assertIsNone(picture2kml.read_coordinates(self.gps((undefined, 0, 0), (2, 0, 0))))

    def test_out_of_range(self):
        self.assertIsNone(picture2kml.read_coordinates(self.gps((91, 0, 0), (2, 0, 0))))

    def test_empty(self):
        self.assertIsNone(picture2kml.read_coordinates({}))


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)

    def run_main(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = picture2kml.main([str(self.directory), *args])
        return status, stdout.getvalue(), stderr.getvalue()

    def placemarks(self):
        root = ET.parse(self.directory / "photos.kml").getroot()
        return root.findall(".//kml:Placemark", NS)

    def test_coordinates_time_and_altitude(self):
        save_picture(
            self.directory / "palma.jpg",
            latitude=("N", (39, 34, 12)),
            longitude=("E", (2, 39, 0)),
            altitude=25.5,
            taken="2024:05:17 10:30:00",
            offset="+02:00",
        )
        self.assertEqual(self.run_main()[0], 0)

        [placemark] = self.placemarks()
        self.assertEqual(placemark.findtext("kml:name", namespaces=NS), "palma.jpg")
        self.assertEqual(
            placemark.findtext(".//kml:coordinates", namespaces=NS),
            "2.6500000,39.5700000,25.5",
        )
        self.assertEqual(
            placemark.findtext(".//kml:when", namespaces=NS), "2024-05-17T10:30:00+02:00"
        )

    def test_south_west_and_below_sea_level(self):
        save_picture(
            self.directory / "ship.jpg",
            latitude=("S", (34, 54, 0)),
            longitude=("W", (56, 12, 0)),
            altitude=-3,
        )
        self.run_main()
        [placemark] = self.placemarks()
        self.assertEqual(
            placemark.findtext(".//kml:coordinates", namespaces=NS),
            "-56.2000000,-34.9000000,-3.0",
        )
        self.assertIsNone(placemark.find(".//kml:TimeStamp", NS))

    def test_placemarks_sorted_by_time_with_undated_last(self):
        tags = {"latitude": ("N", (39, 0, 0)), "longitude": ("E", (2, 0, 0))}
        save_picture(self.directory / "a.jpg", **tags)
        save_picture(self.directory / "b.jpg", **tags, taken="2024:05:17 10:30:00")
        save_picture(self.directory / "c.jpg", **tags, taken="2023:01:01 08:00:00")
        self.run_main()
        names = [p.findtext("kml:name", namespaces=NS) for p in self.placemarks()]
        self.assertEqual(names, ["c.jpg", "b.jpg", "a.jpg"])

    def test_sorted_by_actual_time_across_utc_offsets(self):
        tags = {"latitude": ("N", (39, 0, 0)), "longitude": ("E", (2, 0, 0))}
        # 10:00+02:00 is 08:00 UTC, which is before 09:00+00:00.
        save_picture(self.directory / "a.jpg", **tags, taken="2024:05:17 09:00:00", offset="+00:00")
        save_picture(self.directory / "b.jpg", **tags, taken="2024:05:17 10:00:00", offset="+02:00")
        self.run_main()
        names = [p.findtext("kml:name", namespaces=NS) for p in self.placemarks()]
        self.assertEqual(names, ["b.jpg", "a.jpg"])

    def save_trip(self):
        save_picture(
            self.directory / "second.jpg",
            latitude=("N", (40, 0, 0)),
            longitude=("E", (3, 0, 0)),
            taken="2024:05:18 10:00:00",
        )
        save_picture(
            self.directory / "first.jpg",
            latitude=("N", (39, 0, 0)),
            longitude=("E", (2, 0, 0)),
            altitude=10,
            taken="2024:05:17 10:00:00",
        )
        save_picture(
            self.directory / "undated.jpg",
            latitude=("N", (41, 0, 0)),
            longitude=("E", (4, 0, 0)),
        )

    def line(self):
        root = ET.parse(self.directory / "photos.kml").getroot()
        return root.find(".//kml:LineString/kml:coordinates", NS)

    def test_no_path_by_default(self):
        self.save_trip()
        self.run_main()
        self.assertIsNone(self.line())

    def test_path_follows_capture_order_and_skips_undated(self):
        self.save_trip()
        status, stdout, _ = self.run_main("--path")
        self.assertEqual(status, 0)
        self.assertEqual(self.line().text, "2.0000000,39.0000000,10.0 3.0000000,40.0000000")
        self.assertIn("1 undated pictures left out of the path", stdout)
        names = [p.findtext("kml:name", namespaces=NS) for p in self.placemarks()]
        self.assertEqual(names, ["Path", "first.jpg", "second.jpg", "undated.jpg"])

    def test_path_only_has_no_points(self):
        self.save_trip()
        self.assertEqual(self.run_main("--path-only")[0], 0)
        [placemark] = self.placemarks()
        self.assertEqual(placemark.findtext("kml:name", namespaces=NS), "Path")
        self.assertIsNone(placemark.find(".//kml:Point", NS))

    def test_path_needs_two_dated_pictures(self):
        save_picture(
            self.directory / "only.jpg",
            latitude=("N", (39, 0, 0)),
            longitude=("E", (2, 0, 0)),
            taken="2024:05:17 10:00:00",
        )
        status, _, stderr = self.run_main("--path")
        self.assertEqual(status, 1)
        self.assertIn("at least 2", stderr)
        self.assertFalse((self.directory / "photos.kml").exists())

    def test_unusual_files_are_reported_not_fatal(self):
        save_picture(
            self.directory / "R&D <1>.JPG",
            latitude=("N", (39, 0, 0)),
            longitude=("E", (2, 0, 0)),
        )
        save_picture(self.directory / "no_gps.jpg")
        (self.directory / "broken.jpg").write_bytes(b"not a picture")
        (self.directory / "notes.txt").write_text("ignored")

        status, stdout, stderr = self.run_main()
        self.assertEqual(status, 0)
        self.assertIn("3 pictures: 1 geotagged, 1 without GPS data, 1 unreadable", stdout)
        self.assertIn("broken.jpg", stderr)
        [placemark] = self.placemarks()
        self.assertEqual(placemark.findtext("kml:name", namespaces=NS), "R&D <1>.JPG")

    def test_recursive_only_when_asked(self):
        (self.directory / "trip").mkdir()
        save_picture(
            self.directory / "trip" / "a.jpg",
            latitude=("N", (39, 0, 0)),
            longitude=("E", (2, 0, 0)),
        )
        self.assertEqual(self.run_main()[0], 1)
        self.assertEqual(self.run_main("--recursive")[0], 0)
        self.assertIn("File: trip/a.jpg", ET.tostring(self.placemarks()[0], "unicode"))

    def test_no_geotagged_pictures_writes_nothing(self):
        save_picture(self.directory / "no_gps.jpg")
        self.assertEqual(self.run_main()[0], 1)
        self.assertFalse((self.directory / "photos.kml").exists())


if __name__ == "__main__":
    unittest.main()

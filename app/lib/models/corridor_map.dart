import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' show Offset;

import 'package:flutter/services.dart' show rootBundle;

/// The bundled offline map pack: a stitched grid of real OpenStreetMap
/// tiles (see app/tool/fetch_corridor_map.py) plus the exact geographic
/// bounds of the stitched image, so lat/lon can be projected onto image
/// pixels precisely — not a schematic, an actual map.
class CorridorMap {
  final String assetPath;
  final double north, south, east, west;
  final int widthPx, heightPx;
  final String attribution;

  const CorridorMap({
    required this.assetPath,
    required this.north,
    required this.south,
    required this.east,
    required this.west,
    required this.widthPx,
    required this.heightPx,
    required this.attribution,
  });

  static Future<CorridorMap> load({
    String jsonAsset = 'assets/map/corridor.json',
    String imageAsset = 'assets/map/corridor.png',
  }) async {
    final raw = await rootBundle.loadString(jsonAsset);
    final m = jsonDecode(raw) as Map<String, dynamic>;
    return CorridorMap(
      assetPath: imageAsset,
      north: (m['north'] as num).toDouble(),
      south: (m['south'] as num).toDouble(),
      east: (m['east'] as num).toDouble(),
      west: (m['west'] as num).toDouble(),
      widthPx: m['width_px'] as int,
      heightPx: m['height_px'] as int,
      attribution: m['attribution'] as String? ?? '© OpenStreetMap contributors',
    );
  }

  // Web Mercator normalised-y (0 at north pole side, 1 at south) — matches
  // the projection the source tiles are already in, so this lines up
  // exactly with pixels in corridor.png rather than approximating with a
  // flat equirectangular projection.
  static double _mercatorY(double latDeg) {
    final latRad = latDeg * math.pi / 180;
    final mercN = math.log(math.tan(math.pi / 4 + latRad / 2));
    return 0.5 - mercN / (2 * math.pi);
  }

  /// Projects a lat/lon onto pixel coordinates within corridor.png.
  Offset project(double lat, double lon) {
    final xFrac = (lon - west) / (east - west);
    final yTop = _mercatorY(north);
    final yBottom = _mercatorY(south);
    final yFrac = (_mercatorY(lat) - yTop) / (yBottom - yTop);
    return Offset(xFrac * widthPx, yFrac * heightPx);
  }

  /// Pixels per kilometre at the image's centre latitude — used to size
  /// the "you are here" viewport in real-world terms.
  double get pxPerKm {
    final centreLat = (north + south) / 2;
    final oneKmInDegLat = 1 / 111.32; // good enough at this scale
    final p1 = project(centreLat, west);
    final p2 = project(centreLat + oneKmInDegLat, west);
    return (p2 - p1).distance;
  }
}

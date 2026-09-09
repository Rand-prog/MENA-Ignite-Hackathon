import 'dart:math' as math;

/// Great-circle distance in metres.
double haversineMeters(double lat1, double lon1, double lat2, double lon2) {
  const r = 6371000.0; // Earth radius, metres
  final phi1 = lat1 * math.pi / 180;
  final phi2 = lat2 * math.pi / 180;
  final dPhi = (lat2 - lat1) * math.pi / 180;
  final dLambda = (lon2 - lon1) * math.pi / 180;
  final a =
      math.sin(dPhi / 2) * math.sin(dPhi / 2) +
      math.cos(phi1) * math.cos(phi2) * math.sin(dLambda / 2) * math.sin(dLambda / 2);
  final c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a));
  return r * c;
}

/// Initial bearing in degrees (0 = north, 90 = east) from point 1 to point 2.
double bearingDegrees(double lat1, double lon1, double lat2, double lon2) {
  final phi1 = lat1 * math.pi / 180;
  final phi2 = lat2 * math.pi / 180;
  final dLambda = (lon2 - lon1) * math.pi / 180;
  final y = math.sin(dLambda) * math.cos(phi2);
  final x =
      math.cos(phi1) * math.sin(phi2) -
      math.sin(phi1) * math.cos(phi2) * math.cos(dLambda);
  final theta = math.atan2(y, x);
  return (theta * 180 / math.pi + 360) % 360;
}

/// The compass point a [bearingDegrees] value falls in — "N", "NE", "E"…
///
/// Exists because the offline map used to render that bearing as a rotated
/// arrow and nothing else, then label the distance "1.4 km ahead". Nothing
/// on screen said the frame was north-up, so the arrow read as a turn
/// instruction; a traveller who had turned around was being told the
/// opposite of the truth. Naming the direction is what makes the arrow a
/// map bearing.
///
/// Eight points, not sixteen: this is read at a glance from a windshield
/// mount, and "NNE" versus "NE" is the same decision to a driver.
String compassPoint(double bearing) {
  const points = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  return points[(((bearing % 360) + 22.5) ~/ 45) % 8];
}

/// How far along the entry->exit gate line a position projects, as a
/// fraction [0, 1] clamped to the segment. Used to place the traveller's
/// dot on the schematic corridor line — an approximation (equirectangular
/// projection), which is fine at highway-corridor scale.
double corridorProgress({
  required double entryLat,
  required double entryLon,
  required double exitLat,
  required double exitLon,
  required double lat,
  required double lon,
}) {
  final ax = entryLon, ay = entryLat;
  final bx = exitLon, by = exitLat;
  final px = lon, py = lat;
  final abx = bx - ax, aby = by - ay;
  final apx = px - ax, apy = py - ay;
  final abLenSq = abx * abx + aby * aby;
  if (abLenSq == 0) return 0;
  final t = (apx * abx + apy * aby) / abLenSq;
  return t.clamp(0.0, 1.0);
}

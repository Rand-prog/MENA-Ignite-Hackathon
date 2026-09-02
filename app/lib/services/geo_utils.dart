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

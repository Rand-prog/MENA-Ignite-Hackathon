/// Mirrors GET /zones. Fetched once at onboarding and cached — the app
/// never hardcodes corridor geometry, per the backend's zone registry
/// being the single source of truth.
class Zone {
  final String zoneId;
  final String label;
  final double entryLat;
  final double entryLon;
  final double exitLat;
  final double exitLon;
  final int gateRadiusM;
  final int corridorKm;
  final int nominalCrossingMin;

  const Zone({
    required this.zoneId,
    required this.label,
    required this.entryLat,
    required this.entryLon,
    required this.exitLat,
    required this.exitLon,
    required this.gateRadiusM,
    required this.corridorKm,
    required this.nominalCrossingMin,
  });

  factory Zone.fromJson(Map<String, dynamic> json) => Zone(
    zoneId: json['zone_id'] as String,
    label: json['label'] as String,
    entryLat: (json['entry_gate']['lat'] as num).toDouble(),
    entryLon: (json['entry_gate']['lon'] as num).toDouble(),
    exitLat: (json['exit_gate']['lat'] as num).toDouble(),
    exitLon: (json['exit_gate']['lon'] as num).toDouble(),
    gateRadiusM: json['gate_radius_m'] as int,
    corridorKm: json['corridor_km'] as int,
    nominalCrossingMin: json['nominal_crossing_min'] as int,
  );

  Map<String, dynamic> toJson() => {
    'zone_id': zoneId,
    'label': label,
    'entry_gate': {'lat': entryLat, 'lon': entryLon},
    'exit_gate': {'lat': exitLat, 'lon': exitLon},
    'gate_radius_m': gateRadiusM,
    'corridor_km': corridorKm,
    'nominal_crossing_min': nominalCrossingMin,
  };
}

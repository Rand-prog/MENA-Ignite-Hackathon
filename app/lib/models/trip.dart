/// Mirrors GET /travellers/me/trip and the demo /trips/{id} shape. The
/// phone never computes any of this — it's a read of what the backend's
/// trip state machine already decided. See docs/SignalGuard_Technical_
/// Feasibility.pdf §3.
class Trip {
  final String tripId;
  final String state;
  final String? risk;
  final int? batteryAtEntry;
  final String? batteryBand;
  final String? congestionTier;
  final String? entryPoint;
  final String? lastKnownLocation;
  final int? predictedCrossingMin;
  final int? monitoringWindowMin;
  final String? decisionRecord;
  final List<String> notifications;

  const Trip({
    required this.tripId,
    required this.state,
    this.risk,
    this.batteryAtEntry,
    this.batteryBand,
    this.congestionTier,
    this.entryPoint,
    this.lastKnownLocation,
    this.predictedCrossingMin,
    this.monitoringWindowMin,
    this.decisionRecord,
    this.notifications = const [],
  });

  bool get isActive => state == 'ACTIVE' || state == 'BUFFER';
  bool get isExited => state == 'EXITED' || state == 'RESOLVED';

  factory Trip.fromJson(Map<String, dynamic> json) => Trip(
    tripId: json['trip_id'] as String,
    state: json['state'] as String,
    risk: json['risk'] as String?,
    batteryAtEntry: json['battery_at_entry'] as int?,
    batteryBand: json['battery_band'] as String?,
    congestionTier: json['congestion_tier'] as String?,
    entryPoint: json['entry_point'] as String?,
    lastKnownLocation: json['last_known_location'] as String?,
    predictedCrossingMin: json['predicted_crossing_min'] as int?,
    monitoringWindowMin: json['monitoring_window_min'] as int?,
    decisionRecord: json['decision_record'] as String?,
    notifications:
        (json['notifications'] as List?)?.map((e) => e.toString()).toList() ??
        const [],
  );
}

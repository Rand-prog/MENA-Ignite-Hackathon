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

  /// The actual moment a contact would be texted, and the server's own
  /// clock at the time this was read.
  ///
  /// Both are needed, and the second is the reason this isn't just a
  /// duration: the demo runs on a virtual clock that can be hours ahead of
  /// the handset's wall time, so computing "how long left" against
  /// DateTime.now() would show nonsense. Every countdown in the app is
  /// (deadline - serverNow) shifted by how long ago the poll landed.
  final DateTime? windowDeadline;
  final DateTime? tier0Deadline;
  final DateTime? tier1Deadline;
  final DateTime? serverNow;

  /// Wall-clock instant this Trip was received, so a countdown keeps
  /// ticking between polls instead of freezing at the last read value.
  final DateTime receivedAt;

  final int plannedStopMin;
  final int tier0GraceSec;

  const Trip({
    required this.tripId,
    required this.state,
    required this.receivedAt,
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
    this.windowDeadline,
    this.tier0Deadline,
    this.tier1Deadline,
    this.serverNow,
    this.plannedStopMin = 0,
    this.tier0GraceSec = 90,
  });

  bool get isActive => state == 'ACTIVE' || state == 'BUFFER';
  bool get isExited => state == 'EXITED' || state == 'RESOLVED';

  /// The backend is asking this handset to confirm the traveller is fine,
  /// before it tells any human anything.
  bool get isTier0 => state == 'TIER0_CHECKING';

  /// Did anyone actually get contacted? "tier0" doesn't count — that rung
  /// exists precisely because nobody was.
  bool get anyHumanNotified =>
      notifications.any((n) => n != 'tier0');

  /// How long until [deadline], measured on the *server's* clock and then
  /// advanced by however long ago this Trip arrived. Null when there is no
  /// deadline or no server clock to anchor to.
  Duration? _remaining(DateTime? deadline) {
    final now = serverNow;
    if (deadline == null || now == null) return null;
    final sincePoll = DateTime.now().difference(receivedAt);
    final left = deadline.difference(now) - sincePoll;
    return left.isNegative ? Duration.zero : left;
  }

  /// Time left before a contact is texted. This is the number that makes
  /// the product legible — "we'd text Omar in 42 minutes" rather than
  /// "115 min window", which is arithmetic the traveller has to do while
  /// driving.
  Duration? get timeUntilContactAlerted => _remaining(windowDeadline);

  /// Time left to answer a Tier 0 ping before it escalates to a human.
  Duration? get timeUntilTier0Expires => _remaining(tier0Deadline);

  /// Wall-clock time a contact would be alerted, in the handset's own
  /// timezone. Derived from the remaining duration rather than from
  /// windowDeadline directly, so the virtual demo clock never leaks a
  /// nonsense hour onto the screen.
  DateTime? get contactAlertAt {
    final left = timeUntilContactAlerted;
    return left == null ? null : DateTime.now().add(left);
  }

  static DateTime? _parse(dynamic v) =>
      v == null ? null : DateTime.tryParse(v as String);

  factory Trip.fromJson(Map<String, dynamic> json) => Trip(
    tripId: json['trip_id'] as String,
    state: json['state'] as String,
    receivedAt: DateTime.now(),
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
    windowDeadline: _parse(json['window_deadline']),
    tier0Deadline: _parse(json['tier0_deadline']),
    tier1Deadline: _parse(json['tier1_deadline']),
    serverNow: _parse(json['server_now']),
    plannedStopMin: (json['planned_stop_min'] as int?) ?? 0,
    tier0GraceSec: (json['tier0_grace_sec'] as int?) ?? 90,
  );
}

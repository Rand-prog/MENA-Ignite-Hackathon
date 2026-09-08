import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../models/corridor_map.dart';
import '../models/trip.dart';
import '../models/zone.dart';
import '../services/geo_utils.dart';
import '../theme/app_theme.dart';
import '../widgets/corridor_painter.dart';
import '../widgets/map_viewport.dart';
import '../widgets/tick.dart';
import 'approach_screen.dart' show formatClock, formatLeft;

/// Screen 3 — Offline map. Works with the radio off: own GPS position on a
/// real, bundled map (actual OpenStreetMap tiles — see
/// app/tool/fetch_corridor_map.py), and an arrow to the nearest
/// reconnection point. No network calls are attempted here — see
/// docs/SignalGuard_User_Flow Phase 3.
class OfflineMapScreen extends StatefulWidget {
  final Zone? zone;
  final Position? position;

  /// Last known trip state, kept from before the radio went dark.
  ///
  /// The deadline it carries is the single most useful thing this screen
  /// can show, and it is usable offline precisely because it is not a live
  /// value: the backend already committed to a wall-clock moment when the
  /// trip went ACTIVE, so counting down to it needs nothing from the
  /// network. Knowing *when* someone will be told is what turns being out
  /// of signal from an open-ended worry into a deadline a person can plan
  /// against — and it matters most here, where they cannot check.
  final Trip? trip;

  /// Ticks once a second so _ContactCountdown can rebuild on its own — see
  /// widgets/tick.dart's doc comment. Null (this widget's own tests and
  /// any other caller that doesn't pass one) renders the countdown once,
  /// statically, rather than crashing or silently not counting down.
  final Listenable? tick;

  const OfflineMapScreen({
    super.key,
    this.zone,
    this.position,
    this.trip,
    this.tick,
  });

  @override
  State<OfflineMapScreen> createState() => _OfflineMapScreenState();
}

class _OfflineMapScreenState extends State<OfflineMapScreen> {
  CorridorMap? _map;

  @override
  void initState() {
    super.initState();
    CorridorMap.load().then((m) {
      if (mounted) setState(() => _map = m);
    });
  }

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final z = widget.zone;
    final pos = widget.position;
    final map = _map;

    double? distanceToNearestKm;
    bool headingToExit = true;
    double? bearing;
    Offset? focusPx;
    Offset? targetPx;
    // Zoomed to always keep the actual destination gate in frame — a
    // fixed 16km window meant the target was usually off-screen, which
    // left the map showing real terrain but no actual route. This is
    // what makes the map itself useful rather than decorative.
    double viewKm = 16;

    if (z != null && pos != null) {
      final progress = corridorProgress(
        entryLat: z.entryLat,
        entryLon: z.entryLon,
        exitLat: z.exitLat,
        exitLon: z.exitLon,
        lat: pos.latitude,
        lon: pos.longitude,
      );
      headingToExit = progress >= 0.5;
      final targetLat = headingToExit ? z.exitLat : z.entryLat;
      final targetLon = headingToExit ? z.exitLon : z.entryLon;
      distanceToNearestKm =
          haversineMeters(pos.latitude, pos.longitude, targetLat, targetLon) /
          1000;
      bearing = bearingDegrees(
        pos.latitude,
        pos.longitude,
        targetLat,
        targetLon,
      );
      viewKm = (distanceToNearestKm * 2.4 + 4).clamp(10, 70);
      if (map != null) {
        focusPx = map.project(pos.latitude, pos.longitude);
        targetPx = map.project(targetLat, targetLon);
      }
    }

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 10,
                  height: 10,
                  decoration: BoxDecoration(
                    color: c.textMuted,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  'No signal — offline map active',
                  style: TextStyle(
                    color: c.textSecondary,
                    fontWeight: FontWeight.w600,
                    fontSize: 13,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              z?.label ?? 'Dead zone',
              style: Theme.of(context).textTheme.headlineMedium,
            ),
            const SizedBox(height: 14),
            Tick(
              tick: widget.tick,
              builder: (_) => _ContactCountdown(trip: widget.trip),
            ),
            const SizedBox(height: 18),
            if (map == null)
              SizedBox(
                height: 280,
                child: Center(
                  child: CircularProgressIndicator(color: c.accent),
                ),
              )
            else
              MapViewport(
                map: map,
                focusPx:
                    focusPx ??
                    Offset.lerp(
                      map.project(z!.entryLat, z.entryLon),
                      map.project(z.exitLat, z.exitLon),
                      0.5,
                    ),
                height: 280,
                viewKm: viewKm,
                routeTargetPx: targetPx,
                markers: [
                  MarkerSpec(
                    map.project(z!.entryLat, z.entryLon),
                    (_) => const GateMarker(label: 'ENTRY'),
                  ),
                  MarkerSpec(
                    map.project(z.exitLat, z.exitLon),
                    (_) => const GateMarker(label: 'EXIT'),
                  ),
                ],
              ),
            const SizedBox(height: 28),
            Row(
              children: [
                if (bearing != null) BearingArrow(bearingDegrees: bearing),
                const SizedBox(width: 20),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Nearest reconnection point',
                        style: TextStyle(color: c.textSecondary, fontSize: 13),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        distanceToNearestKm != null
                            ? '${distanceToNearestKm.toStringAsFixed(1)} km '
                                  '${headingToExit ? "ahead" : "behind"}'
                            : 'Locating…',
                        style: Theme.of(context).textTheme.titleLarge,
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const Spacer(),
            Text(
              'If something goes wrong, head in the direction of the '
              'arrow. Your position works without signal — this screen '
              'needs nothing from the network.',
              style: TextStyle(color: c.textMuted, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}


/// "Your contact hears from us at 15:40 — that's 38 minutes away."
///
/// Counts down against the deadline the backend fixed at entry, so it works
/// with the radio off. Shown here and not only on the online screen because
/// this is where a traveller actually wants it: mid-crossing, unable to
/// check anything, wondering whether the people at home are about to start
/// worrying.
class _ContactCountdown extends StatelessWidget {
  final Trip? trip;
  const _ContactCountdown({this.trip});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final t = trip;
    final left = t?.timeUntilContactAlerted;
    final at = t?.contactAlertAt;

    if (t == null || left == null || at == null) {
      return const SizedBox.shrink();
    }

    final expired = left.inSeconds <= 0 || t.anyHumanNotified;
    final tone = expired ? c.amber : c.textSecondary;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: c.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: expired ? c.amber.withValues(alpha: 0.4) : c.border,
        ),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            expired ? Icons.notifications_active_rounded : Icons.schedule_rounded,
            size: 18,
            color: tone,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  expired
                      ? 'Your contact has been told you\'re overdue.'
                      : 'Your contact hears from us at ${formatClock(at)}.',
                  style: TextStyle(
                    color: expired ? c.amber : c.textPrimary,
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  expired
                      ? 'Reconnect when you can and we\'ll send the all-clear '
                          'automatically.'
                      : '${formatLeft(left)} from now. Reconnect before then '
                          'and nobody hears anything.',
                  style: TextStyle(color: c.textSecondary, fontSize: 12, height: 1.4),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

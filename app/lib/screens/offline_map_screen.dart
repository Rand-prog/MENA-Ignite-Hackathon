import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../models/corridor_map.dart';
import '../models/zone.dart';
import '../services/geo_utils.dart';
import '../theme/app_theme.dart';
import '../widgets/corridor_painter.dart';
import '../widgets/map_viewport.dart';

/// Screen 3 — Offline map. Works with the radio off: own GPS position on a
/// real, bundled map (actual OpenStreetMap tiles — see
/// app/tool/fetch_corridor_map.py), and an arrow to the nearest
/// reconnection point. No network calls are attempted here — see
/// docs/SignalGuard_User_Flow Phase 3.
class OfflineMapScreen extends StatefulWidget {
  final Zone? zone;
  final Position? position;

  const OfflineMapScreen({super.key, this.zone, this.position});

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
                  decoration: const BoxDecoration(
                    color: AppColors.textMuted,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                const Text(
                  'No signal — offline map active',
                  style: TextStyle(
                    color: AppColors.textSecondary,
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
            const SizedBox(height: 28),
            if (map == null)
              const SizedBox(
                height: 280,
                child: Center(
                  child: CircularProgressIndicator(color: AppColors.accent),
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
                      const Text(
                        'Nearest reconnection point',
                        style: TextStyle(
                          color: AppColors.textSecondary,
                          fontSize: 13,
                        ),
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
            const Text(
              'If something goes wrong, head in the direction of the '
              'arrow. Your position works without signal — this screen '
              'needs nothing from the network.',
              style: TextStyle(color: AppColors.textMuted, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}

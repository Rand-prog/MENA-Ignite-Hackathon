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
import 'approach_screen.dart'
    show ThemeButton, contactLabel, formatClock, formatLeft;

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

  /// Theme control, threaded in from HomeShell.
  ///
  /// The toggle used to live only on ApproachScreen, so it disappeared the
  /// instant connectivity dropped and the shell swapped this screen in —
  /// leaving no way to reach daylight mode on the one screen app_theme.dart
  /// names as the worst affected by a near-black palette at midday. Both
  /// optional: with no [onCycleTheme] the button simply renders disabled,
  /// which is what this screen's own tests get.
  final ThemeMode themeMode;
  final VoidCallback? onCycleTheme;

  /// The emergency contact's own name, for the countdown copy — see
  /// approach_screen.dart's [contactLabel]. Null falls back to "your
  /// contact".
  final String? contactName;

  const OfflineMapScreen({
    super.key,
    this.zone,
    this.position,
    this.trip,
    this.tick,
    this.themeMode = ThemeMode.dark,
    this.onCycleTheme,
    this.contactName,
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
    // Which gate the distance and bearing below actually refer to. Named
    // for what it is — the nearer end of the corridor along the entry->exit
    // line — rather than for a direction of travel: this is derived from
    // position alone and says nothing about which way the car is pointing.
    bool targetIsExit = true;
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
      targetIsExit = progress >= 0.5;
      final targetLat = targetIsExit ? z.exitLat : z.entryLat;
      final targetLon = targetIsExit ? z.exitLon : z.entryLon;
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

    // Scrollable, not a fixed Column.
    //
    // The children below sum to roughly 650-700 logical pixels before
    // safe-area insets — a 280px map, an 84px bearing dial, header,
    // headline and the countdown card — so a short handset, or any phone
    // with system text scaling much above 1.3, overflowed. What got cut was
    // the bottom: the bearing dial, the distance, and the instruction to
    // follow it. That is the entire payload of this screen, replaced by
    // overflow stripes, with no way to scroll down to it — on the one
    // screen whose job is to work when everything else has failed. Same
    // LayoutBuilder/minHeight pattern and same reasoning as
    // approach_screen.dart.
    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          return SingleChildScrollView(
            child: ConstrainedBox(
              constraints: BoxConstraints(minHeight: constraints.maxHeight),
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
                        // Expanded, because the theme button now shares this
                        // row and the two together do not fit a 375px screen
                        // otherwise.
                        Expanded(
                          child: Text(
                            'No signal — offline map active',
                            style: TextStyle(
                              color: c.textSecondary,
                              fontWeight: FontWeight.w600,
                              fontSize: 13,
                            ),
                          ),
                        ),
                        ThemeButton(
                          mode: widget.themeMode,
                          onPressed: widget.onCycleTheme,
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      // "Dead zone" as a fallback headline would assert the
                      // traveller is in one, on a screen that is shown for
                      // any loss of signal anywhere. With no cached zone the
                      // honest headline is what the screen is, not where the
                      // traveller is.
                      z?.label ?? 'Offline map',
                      style: Theme.of(context).textTheme.headlineMedium,
                    ),
                    const SizedBox(height: 14),
                    Tick(
                      tick: widget.tick,
                      builder: (_) => _ContactCountdown(
                        trip: widget.trip,
                        contactName: widget.contactName,
                        hasCorridor: z != null,
                      ),
                    ),
                    const SizedBox(height: 18),
                    if (map == null)
                      SizedBox(
                        height: 280,
                        child: Center(
                          child: CircularProgressIndicator(color: c.accent),
                        ),
                      )
                    // No cached zone: every use of `z` below is a force
                    // unwrap, and onboarding only caches one when /zones came
                    // back non-empty (onboarding_screen.dart). So a traveller
                    // who registered while the backend had nothing armed used
                    // to get a red null-check error screen the first time
                    // they lost signal. Say what is missing instead — honest
                    // and non-fatal beats a crash, especially here.
                    else if (z == null)
                      const _NoCorridorPanel()
                    else
                      MapViewport(
                        map: map,
                        focusPx:
                            focusPx ??
                            Offset.lerp(
                              map.project(z.entryLat, z.entryLon),
                              map.project(z.exitLat, z.exitLon),
                              0.5,
                            ),
                        height: 280,
                        viewKm: viewKm,
                        routeTargetPx: targetPx,
                        markers: [
                          MarkerSpec(
                            map.project(z.entryLat, z.entryLon),
                            (_) => const GateMarker(label: 'ENTRY'),
                          ),
                          MarkerSpec(
                            map.project(z.exitLat, z.exitLon),
                            (_) => const GateMarker(label: 'EXIT'),
                          ),
                        ],
                      ),
                    // No zone means no gate to measure to, so there is
                    // nothing truthful to put in this block at all.
                    if (z != null) ...[
                      const SizedBox(height: 28),
                      _DirectionBlock(
                        bearing: bearing,
                        distanceKm: distanceToNearestKm,
                        targetIsExit: targetIsExit,
                      ),
                    ],
                    // Was a Spacer, which is unbounded inside a scroll view
                    // and throws outright.
                    const SizedBox(height: 28),
                    Text(
                      bearing != null
                          ? 'If something goes wrong, head in the direction '
                              'of the arrow. Your position works without '
                              'signal — this screen needs nothing from the '
                              'network.'
                          // No bearing, no arrow — telling someone to follow
                          // one that is not on screen is worse than saying
                          // less.
                          : 'Your position works without signal — this '
                              'screen needs nothing from the network.',
                      style: TextStyle(color: c.textMuted, height: 1.5),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

/// Distance and bearing to the gate the map is pointing at.
///
/// This used to read "1.4 km ahead" / "1.4 km behind", which asserted a
/// direction of travel the app does not have: the choice of gate comes from
/// `corridorProgress` — which end of the corridor the traveller's *position*
/// projects nearer to — and a traveller who has turned around was being told
/// the exact opposite of the truth. The distance is now labelled with the
/// gate it actually measures to, using the same ENTRY/EXIT words as the
/// markers on the map directly above it.
///
/// The dial is a north-up map bearing, not a turn to make, so it carries the
/// compass point and degrees in writing and an "N" reference on the dial
/// itself (see BearingArrow).
class _DirectionBlock extends StatelessWidget {
  final double? bearing;
  final double? distanceKm;
  final bool targetIsExit;

  const _DirectionBlock({
    required this.bearing,
    required this.distanceKm,
    required this.targetIsExit,
  });

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final b = bearing;
    final km = distanceKm;
    final gate = targetIsExit ? 'EXIT' : 'ENTRY';
    final distanceText = km != null
        ? (targetIsExit
            ? '${km.toStringAsFixed(1)} km to the EXIT gate'
            : '${km.toStringAsFixed(1)} km back to the ENTRY gate')
        : 'Locating…';
    final compass =
        b == null ? null : '${compassPoint(b)} · ${_degrees(b)}°';

    return Semantics(
      label: b == null
          ? 'Nearest reconnection point. $distanceText.'
          : 'Nearest reconnection point. $distanceText. '
              'Map bearing to the $gate gate: ${compassPoint(b)}, '
              '${_degrees(b)} degrees.',
      excludeSemantics: true,
      child: Row(
        children: [
          if (b != null)
            Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                BearingArrow(bearingDegrees: b),
                const SizedBox(height: 6),
                Text(
                  compass!,
                  style: TextStyle(
                    color: c.textSecondary,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.3,
                  ),
                ),
              ],
            ),
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
                  distanceText,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Zero-padded, and folded back to 0 rather than showing "360°" for a
  /// bearing that rounds up off the end of the circle.
  String _degrees(double b) =>
      (b.round() % 360).toString().padLeft(3, '0');
}

/// Shown in the map's slot when this phone has no cached corridor.
///
/// See the branch that builds it: the alternative was a null-check crash,
/// and before that a blank space that said nothing about why the map was
/// missing.
class _NoCorridorPanel extends StatelessWidget {
  const _NoCorridorPanel();

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: c.surface,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: c.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.map_outlined, size: 18, color: c.textMuted),
              const SizedBox(width: 8),
              Text(
                'No corridor map loaded',
                style: TextStyle(
                  color: c.textPrimary,
                  fontWeight: FontWeight.w600,
                  fontSize: 14,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'No dead zone was cached when this phone registered, so there '
            'is nothing here to place your position or the gates against. '
            'Your GPS is fine — this screen just has no corridor to draw '
            'it on.',
            style: TextStyle(color: c.textSecondary, fontSize: 13, height: 1.45),
          ),
        ],
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
  final String? contactName;
  final bool hasCorridor;
  const _ContactCountdown({
    this.trip,
    this.contactName,
    this.hasCorridor = false,
  });

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final t = trip;

    // No trip at all — see _NoCrossingKnownCard. This slot used to be
    // empty here, which made losing signal in a garage look exactly like
    // being mid-crossing.
    if (t == null) return _NoCrossingKnownCard(hasCorridor: hasCorridor);

    final left = t.timeUntilContactAlerted;
    final at = t.contactAlertAt;

    // A trip exists but has no fixed deadline yet (BUFFER) — the headline
    // and map already carry that, and inventing a countdown for it would
    // be the false precision this product exists not to show.
    if (left == null || at == null) {
      return const SizedBox.shrink();
    }

    final expired = left.inSeconds <= 0 || t.anyHumanNotified;
    final tone = expired ? c.amber : c.textSecondary;
    final name = contactName;

    // Phrased so the contact's name is never the subject of the verb: two
    // saved contacts render as "Omar and Layla", and "Omar and Layla has
    // been told" is broken English on the screen a traveller reads when
    // they cannot check anything else.
    final String headline;
    if (expired) {
      headline = name != null
          ? 'We\'ve told $name you\'re overdue.'
          : 'Your contact has been told you\'re overdue.';
    } else {
      headline = name != null
          ? 'We text $name at ${formatClock(at)}.'
          : 'Your contact hears from us at ${formatClock(at)}.';
    }

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
                  headline,
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

/// What sits in the countdown's slot when there is no trip to count down.
///
/// HomeShell renders this whole screen whenever connectivity drops, trip or
/// no trip — losing signal in an underground car park or on a road with no
/// monitored corridor looks the same to it. The countdown used to render an
/// empty box in that case, leaving a corridor map, ENTRY/EXIT markers and a
/// gate distance on screen with nothing anywhere saying no crossing is
/// being watched. That either falsely reassures or falsely alarms, on the
/// one screen where the traveller cannot go and check.
///
/// The wording is deliberately about what this handset last *heard*, never
/// about what is true. The backend opens a trip from the operator's
/// geofence webhook, so one can be open right now without this phone ever
/// having polled it — "you are not being monitored" would be a claim the
/// app has no way to make.
class _NoCrossingKnownCard extends StatelessWidget {
  /// Whether a corridor is actually drawn below this card — without one
  /// there is no gate on screen, so the closing line would be pointing at
  /// something that is not there.
  final bool hasCorridor;
  const _NoCrossingKnownCard({required this.hasCorridor});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: c.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: c.border),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.help_outline_rounded, size: 18, color: c.textMuted),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'No crossing was open the last time this phone heard '
                  'from the network.',
                  style: TextStyle(
                    color: c.textPrimary,
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    height: 1.35,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  'If one has started since then, this phone has no way to '
                  'know — it can\'t reach the network to ask.'
                  '${hasCorridor ? ' The gates below are still the nearest '
                      'coverage it knows of.' : ''}',
                  style: TextStyle(
                    color: c.textSecondary,
                    fontSize: 12,
                    height: 1.4,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

import 'package:flutter/material.dart';

import '../models/corridor_map.dart';
import '../theme/app_theme.dart';

/// A real, offline "you are here" map: the bundled corridor.png (actual
/// OpenStreetMap tiles, see app/tool/fetch_corridor_map.py) panned and
/// zoomed under a fixed centre dot, the way a turn-by-turn nav app frames
/// your position — rather than a schematic line standing in for a map.
class MapViewport extends StatelessWidget {
  final CorridorMap map;
  final Offset? focusPx; // traveller position in image-pixel space
  final double viewKm; // vertical span of real-world distance to show
  final double height;
  final List<MarkerSpec> markers;
  final Offset? routeTargetPx; // the gate to head toward — drawn as a line

  const MapViewport({
    super.key,
    required this.map,
    required this.focusPx,
    required this.height,
    this.viewKm = 16,
    this.markers = const [],
    this.routeTargetPx,
  });

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final focus = focusPx ?? Offset(map.widthPx / 2, map.heightPx / 2);
    // pxPerKm runs two Mercator projections; hoisted out of the
    // LayoutBuilder below so it is computed once per build rather than on
    // every layout pass.
    final pxPerKm = map.pxPerKm;

    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final vw = constraints.maxWidth;
            final vh = constraints.maxHeight;
            final zoom = (vh / (viewKm * pxPerKm)).clamp(0.05, 4.0);
            final dx = vw / 2 - focus.dx * zoom;
            final dy = vh / 2 - focus.dy * zoom;

            return Container(
              color: c.surface,
              child: Stack(
                clipBehavior: Clip.hardEdge,
                children: [
                  // Positioned + a pre-scaled SizedBox rather than a paint
                  // Matrix4 transform — resizes at layout time instead of
                  // via a canvas transform, which is the robust path on
                  // software-rendered GPUs (SwiftShader) where Impeller's
                  // Transform+large-image combo can render blank.
                  Positioned(
                    left: dx,
                    top: dy,
                    width: map.widthPx * zoom,
                    height: map.heightPx * zoom,
                    child: Image.asset(
                      map.assetPath,
                      fit: BoxFit.fill,
                      filterQuality: FilterQuality.medium,
                    ),
                  ),
                  // Scrim, night only.
                  //
                  // Its job is to keep light-on-dark overlay text legible
                  // over map tiles of unpredictable brightness. In
                  // daylight the overlays are already dark-on-light, so
                  // the scrim protects against a problem that no longer
                  // exists — and washes out the terrain to do it. The map
                  // is the entire point of this screen; dimming it for
                  // nothing is a straight loss.
                  if (!c.isDay)
                    Container(
                      decoration: BoxDecoration(
                        gradient: LinearGradient(
                          begin: Alignment.topCenter,
                          end: Alignment.bottomCenter,
                          colors: [
                            c.bg.withValues(alpha: 0.25),
                            Colors.transparent,
                            c.bg.withValues(alpha: 0.35),
                          ],
                        ),
                      ),
                    ),
                  if (routeTargetPx != null)
                    CustomPaint(
                      size: Size(vw, vh),
                      painter: _RouteLinePainter(
                        color: c.amber,
                        from: Offset(vw / 2, vh / 2),
                        to: Offset(
                          vw / 2 + (routeTargetPx!.dx - focus.dx) * zoom,
                          vh / 2 + (routeTargetPx!.dy - focus.dy) * zoom,
                        ),
                      ),
                    ),
                  for (final m in markers)
                    _positioned(
                      vw / 2 + (m.px.dx - focus.dx) * zoom,
                      vh / 2 + (m.px.dy - focus.dy) * zoom,
                      m.build(context),
                    ),
                  // Fixed centre dot — the traveller. Always dead centre;
                  // the map moves, not the dot, matching how nav apps
                  // read at highway speed.
                  Center(child: _TravellerDot(color: c.accent)),
                  // Scale bar. Distance existed only as the text "1.4 km
                  // ahead"; the map itself could be read for direction and
                  // not for distance, which is half of what a map is for
                  // when you are deciding whether to walk it.
                  Positioned(
                    left: 10,
                    bottom: 8,
                    child: _ScaleBar(
                      pxPerKm: pxPerKm * zoom,
                      color: c.isDay ? Colors.black87 : Colors.white,
                      halo: c.isDay ? Colors.white : Colors.black54,
                    ),
                  ),
                  Positioned(
                    right: 8,
                    bottom: 6,
                    child: Text(
                      map.attribution,
                      // Sits directly on map tiles, so it carries its own
                      // contrast rather than inheriting the theme's text
                      // colour — white-on-white in daylight otherwise.
                      style: TextStyle(
                        fontSize: 9,
                        color: c.isDay ? Colors.black87 : Colors.white70,
                        shadows: [
                          Shadow(
                            blurRadius: 2,
                            color: c.isDay ? Colors.white : Colors.black,
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );
  }

  Widget _positioned(double x, double y, Widget child) {
    return Positioned(
      left: x,
      top: y,
      child: FractionalTranslation(
        translation: const Offset(-0.5, -0.5),
        child: child,
      ),
    );
  }
}

/// Draws the actual line from the traveller to the gate they should be
/// heading toward — this is what makes the map itself useful, not just
/// decorative: without it, "which way" lives only in a text label and a
/// compass icon, and the real geography underneath is never connected to
/// the answer.
class _RouteLinePainter extends CustomPainter {
  final Offset from;
  final Offset to;
  final Color color;
  _RouteLinePainter({required this.from, required this.to, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 3
      ..strokeCap = StrokeCap.round;

    final total = (to - from).distance;
    if (total < 1) return;
    final dir = (to - from) / total;
    const dashLen = 10.0, gapLen = 7.0;
    var travelled = 0.0;
    while (travelled < total) {
      final segEnd = (travelled + dashLen).clamp(0.0, total);
      canvas.drawLine(
        from + dir * travelled,
        from + dir * segEnd,
        paint,
      );
      travelled += dashLen + gapLen;
    }
  }

  @override
  bool shouldRepaint(covariant _RouteLinePainter oldDelegate) =>
      oldDelegate.from != from ||
      oldDelegate.to != to ||
      oldDelegate.color != color;
}

class MarkerSpec {
  final Offset px;
  final Widget Function(BuildContext) build;
  const MarkerSpec(this.px, this.build);
}

class GateMarker extends StatelessWidget {
  final String label;
  const GateMarker({super.key, required this.label});

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 12,
          height: 12,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: const Color(0xFF64756F),
            border: Border.all(color: Colors.black45, width: 2),
          ),
        ),
        const SizedBox(height: 2),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: Colors.black.withValues(alpha: 0.68),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(
            label,
            style: const TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w700,
              color: Colors.white,
              letterSpacing: 0.4,
            ),
          ),
        ),
      ],
    );
  }
}

class _TravellerDot extends StatelessWidget {
  final Color color;
  const _TravellerDot({required this.color});

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: 'Your position',
      child: _dot(),
    );
  }

  Widget _dot() {
    return Container(
      width: 30,
      height: 30,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: color.withValues(alpha: 0.22),
      ),
      child: Container(
        width: 14,
        height: 14,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: color,
          border: Border.all(color: Colors.white, width: 2),
          boxShadow: [
            BoxShadow(color: color.withValues(alpha: 0.6), blurRadius: 8),
          ],
        ),
      ),
    );
  }
}

/// A real scale bar: picks a round distance that fits the current zoom and
/// draws it, so the map can be read for how far as well as which way.
class _ScaleBar extends StatelessWidget {
  final double pxPerKm;
  final Color color;
  final Color halo;
  const _ScaleBar({
    required this.pxPerKm,
    required this.color,
    required this.halo,
  });

  /// Round numbers a person actually thinks in, smallest first.
  static const _steps = [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0];

  @override
  Widget build(BuildContext context) {
    if (pxPerKm <= 0 || !pxPerKm.isFinite) return const SizedBox.shrink();
    // The largest round distance that still fits in ~90px of bar.
    var km = _steps.first;
    for (final s in _steps) {
      if (s * pxPerKm <= 90) km = s;
    }
    final width = km * pxPerKm;
    if (width < 12) return const SizedBox.shrink();

    return Semantics(
      label: 'Map scale: ${km.toStringAsFixed(0)} kilometres',
      excludeSemantics: true,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${km.toStringAsFixed(0)} km',
            style: TextStyle(
              fontSize: 9,
              fontWeight: FontWeight.w700,
              color: color,
              shadows: [Shadow(blurRadius: 2, color: halo)],
            ),
          ),
          const SizedBox(height: 2),
          Container(
            width: width,
            height: 3,
            decoration: BoxDecoration(
              color: color,
              borderRadius: BorderRadius.circular(1.5),
              // Same trick as the label's text shadow: the bar sits over
              // map tiles of unknown brightness, so it carries its own
              // contrast rather than assuming the terrain underneath.
              boxShadow: [BoxShadow(color: halo, blurRadius: 2)],
            ),
          ),
        ],
      ),
    );
  }
}

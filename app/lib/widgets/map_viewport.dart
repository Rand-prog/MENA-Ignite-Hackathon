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
    final focus = focusPx ?? Offset(map.widthPx / 2, map.heightPx / 2);

    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final vw = constraints.maxWidth;
            final vh = constraints.maxHeight;
            final zoom = (vh / (viewKm * map.pxPerKm)).clamp(0.05, 4.0);
            final dx = vw / 2 - focus.dx * zoom;
            final dy = vh / 2 - focus.dy * zoom;

            return Container(
              color: AppColors.surface,
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
                  // Subtle scrim so overlay markers/text stay legible over
                  // whatever the map tile looks like underneath.
                  Container(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(
                        begin: Alignment.topCenter,
                        end: Alignment.bottomCenter,
                        colors: [
                          AppColors.bg.withValues(alpha: 0.25),
                          Colors.transparent,
                          AppColors.bg.withValues(alpha: 0.35),
                        ],
                      ),
                    ),
                  ),
                  if (routeTargetPx != null)
                    CustomPaint(
                      size: Size(vw, vh),
                      painter: _RouteLinePainter(
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
                  Center(child: _TravellerDot()),
                  Positioned(
                    right: 8,
                    bottom: 6,
                    child: Text(
                      map.attribution,
                      style: const TextStyle(
                        fontSize: 9,
                        color: Colors.white70,
                        shadows: [Shadow(blurRadius: 2, color: Colors.black)],
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
  _RouteLinePainter({required this.from, required this.to});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = AppColors.amber
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
      oldDelegate.from != from || oldDelegate.to != to;
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
            color: AppColors.textMuted,
            border: Border.all(color: Colors.black45, width: 2),
          ),
        ),
        const SizedBox(height: 2),
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
          decoration: BoxDecoration(
            color: AppColors.bg.withValues(alpha: 0.75),
            borderRadius: BorderRadius.circular(4),
          ),
          child: Text(
            label,
            style: const TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w700,
              color: AppColors.textSecondary,
              letterSpacing: 0.4,
            ),
          ),
        ),
      ],
    );
  }
}

class _TravellerDot extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 30,
      height: 30,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: AppColors.accent.withValues(alpha: 0.22),
      ),
      child: Container(
        width: 14,
        height: 14,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: AppColors.accent,
          border: Border.all(color: Colors.white, width: 2),
          boxShadow: [
            BoxShadow(
              color: AppColors.accent.withValues(alpha: 0.6),
              blurRadius: 8,
            ),
          ],
        ),
      ),
    );
  }
}

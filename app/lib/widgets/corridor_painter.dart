import 'dart:math' as math;

import 'package:flutter/material.dart';


/// A small compass-style bearing indicator, used alongside the real map
/// viewport (see map_viewport.dart) to show the real-world direction back
/// to signal — useful once someone has left the road.
class BearingArrow extends StatelessWidget {
  final double bearingDegrees;
  const BearingArrow({super.key, required this.bearingDegrees});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 84,
      height: 84,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: const Color(0xFF141B19),
        border: Border.all(color: const Color(0xFF283330)),
      ),
      child: Transform.rotate(
        angle: bearingDegrees * math.pi / 180,
        child: const Icon(
          Icons.navigation_rounded,
          // The bearing arrow sits over map tiles, so it keeps its own
          // fixed amber rather than following the light/dark palette.
          color: Color(0xFFE6B94D),
          size: 36,
        ),
      ),
    );
  }
}

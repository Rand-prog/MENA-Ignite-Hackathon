import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

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
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
      ),
      child: Transform.rotate(
        angle: bearingDegrees * math.pi / 180,
        child: const Icon(
          Icons.navigation_rounded,
          color: AppColors.amber,
          size: 36,
        ),
      ),
    );
  }
}

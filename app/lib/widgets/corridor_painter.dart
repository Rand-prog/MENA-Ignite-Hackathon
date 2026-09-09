import 'dart:math' as math;

import 'package:flutter/material.dart';


/// A small compass-style bearing indicator, used alongside the real map
/// viewport (see map_viewport.dart) to show the real-world direction back
/// to signal — useful once someone has left the road.
///
/// The dial is north-up: the needle points at the true bearing from
/// geo_utils.dart's `bearingDegrees` (0 = north), not at a turn to make.
/// That distinction was invisible — an unlabelled rotated arrow reads as
/// "go this way relative to the car", which is a different and often
/// opposite instruction — so the "N" below is drawn *outside* the rotation
/// and stays pinned to the top of the dial as the fixed reference.
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
      child: Stack(
        alignment: Alignment.center,
        children: [
          Transform.rotate(
            angle: bearingDegrees * math.pi / 180,
            child: const Icon(
              Icons.navigation_rounded,
              // The bearing arrow sits over map tiles, so it keeps its own
              // fixed amber rather than following the light/dark palette.
              color: Color(0xFFE6B94D),
              size: 36,
            ),
          ),
          const Positioned(
            top: 5,
            child: Text(
              'N',
              style: TextStyle(
                fontSize: 10,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.5,
                // Same reasoning as the arrow above: the dial paints its
                // own fixed dark face, so this label carries its own
                // contrast rather than reading the palette and going
                // near-black-on-near-black in daylight.
                color: Color(0xFF9DB0AB),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

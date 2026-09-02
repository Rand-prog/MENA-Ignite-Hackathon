import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Screen 4 — Reconnection. Quiet confirmation only. Per docs/SignalGuard_
/// User_Flow Phase 4a: "the user did absolutely nothing the entire trip.
/// That's the product." No action required here, it just tells them so.
class ReconnectionScreen extends StatelessWidget {
  const ReconnectionScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            TweenAnimationBuilder<double>(
              tween: Tween(begin: 0, end: 1),
              duration: const Duration(milliseconds: 500),
              curve: Curves.easeOutBack,
              builder: (context, v, child) =>
                  Transform.scale(scale: v, child: child),
              child: Container(
                width: 72,
                height: 72,
                decoration: BoxDecoration(
                  color: AppColors.accent.withValues(alpha: 0.15),
                  shape: BoxShape.circle,
                ),
                child: const Icon(
                  Icons.check_rounded,
                  color: AppColors.accent,
                  size: 36,
                ),
              ),
            ),
            const SizedBox(height: 24),
            Text(
              'Welcome back online.',
              style: Theme.of(context).textTheme.headlineMedium,
            ),
            const SizedBox(height: 8),
            const Text(
              'Trip completed safely. Your contact was never notified.',
              style: TextStyle(color: AppColors.textSecondary, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}

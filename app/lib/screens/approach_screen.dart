import 'package:flutter/material.dart';

import '../models/trip.dart';
import '../theme/app_theme.dart';

/// Screen 2 — Approach notification. Also doubles as the app's quiet idle
/// home: "nothing nearby" is the ~all-the-time state per docs/SignalGuard_
/// User_Flow Phase 1 ("the app is invisible"). BUFFER = "preparing you
/// now…"; ACTIVE = "you're offline-ready" with the risk-adjusted window.
class ApproachScreen extends StatelessWidget {
  final Trip? trip;

  const ApproachScreen({super.key, this.trip});

  @override
  Widget build(BuildContext context) {
    final t = trip;
    final preparing = t != null && t.state == 'BUFFER';
    final ready = t != null && t.state == 'ACTIVE';

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.podcasts_rounded,
                  color: AppColors.accent,
                  size: 22,
                ),
                const SizedBox(width: 8),
                Text(
                  'SignalGuard',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ],
            ),
            const Spacer(flex: 2),
            AnimatedSwitcher(
              duration: const Duration(milliseconds: 350),
              child: ready
                  ? _ReadyCard(trip: t)
                  : preparing
                  ? const _PreparingCard()
                  : const _IdleCard(),
            ),
            const Spacer(flex: 3),
          ],
        ),
      ),
    );
  }
}

class _IdleCard extends StatelessWidget {
  const _IdleCard();

  @override
  Widget build(BuildContext context) {
    return Column(
      key: const ValueKey('idle'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: AppColors.border),
          ),
          child: const Icon(
            Icons.shield_moon_outlined,
            color: AppColors.accentDim,
            size: 30,
          ),
        ),
        const SizedBox(height: 20),
        Text(
          'Watching, quietly.',
          style: Theme.of(context).textTheme.headlineMedium,
        ),
        const SizedBox(height: 8),
        const Text(
          'No dead zone nearby. You don\'t need to do anything — close '
          'the app and go.',
          style: TextStyle(color: AppColors.textSecondary, height: 1.5),
        ),
      ],
    );
  }
}

class _PreparingCard extends StatelessWidget {
  const _PreparingCard();

  @override
  Widget build(BuildContext context) {
    return Column(
      key: const ValueKey('preparing'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _PulsingDot(color: AppColors.amber),
        const SizedBox(height: 20),
        Text(
          'Low-coverage zone ahead.',
          style: Theme.of(
            context,
          ).textTheme.headlineMedium?.copyWith(color: AppColors.amber),
        ),
        const SizedBox(height: 8),
        const Text(
          'Preparing you now — downloading the offline map for this '
          'corridor.',
          style: TextStyle(color: AppColors.textSecondary, height: 1.5),
        ),
        const SizedBox(height: 20),
        const LinearProgressIndicator(
          backgroundColor: AppColors.surface,
          color: AppColors.amber,
          minHeight: 3,
        ),
      ],
    );
  }
}

class _ReadyCard extends StatelessWidget {
  final Trip trip;
  const _ReadyCard({required this.trip});

  @override
  Widget build(BuildContext context) {
    final risk = trip.risk ?? 'LOW';
    final isLow = risk == 'LOW';
    return Column(
      key: const ValueKey('ready'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(
          Icons.offline_pin_rounded,
          color: AppColors.accent,
          size: 40,
        ),
        const SizedBox(height: 16),
        Text(
          'You\'re offline-ready.',
          style: Theme.of(context).textTheme.headlineMedium,
        ),
        const SizedBox(height: 14),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _Chip(
              label: 'Risk: $risk',
              color: isLow ? AppColors.accent : AppColors.amber,
            ),
            if (trip.predictedCrossingMin != null)
              _Chip(label: '~${trip.predictedCrossingMin} min', color: AppColors.textSecondary),
          ],
        ),
        const SizedBox(height: 16),
        const Text(
          'Your contact will be alerted only if you don\'t reconnect in '
          'time.',
          style: TextStyle(color: AppColors.textSecondary, height: 1.5),
        ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  final String label;
  final Color color;
  const _Chip({required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontWeight: FontWeight.w600,
          fontSize: 13,
        ),
      ),
    );
  }
}

class _PulsingDot extends StatefulWidget {
  final Color color;
  const _PulsingDot({required this.color});

  @override
  State<_PulsingDot> createState() => _PulsingDotState();
}

class _PulsingDotState extends State<_PulsingDot>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (context, _) {
        final scale = 0.85 + _c.value * 0.3;
        return Transform.scale(
          scale: scale,
          child: Container(
            width: 20,
            height: 20,
            decoration: BoxDecoration(
              color: widget.color,
              shape: BoxShape.circle,
              boxShadow: [
                BoxShadow(
                  color: widget.color.withValues(alpha: 0.5),
                  blurRadius: 16,
                  spreadRadius: 2,
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

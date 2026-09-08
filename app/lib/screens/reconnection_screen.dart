import 'package:flutter/material.dart';

import '../models/trip.dart';
import '../theme/app_theme.dart';

/// Screen 4 — Reconnection. Quiet confirmation only. Per docs/SignalGuard_
/// User_Flow Phase 4a: "the user did absolutely nothing the entire trip.
/// That's the product." No action required here beyond dismissing it.
///
/// It stays until dismissed. It used to disappear on a four-second timer,
/// which assumed the traveller was looking at the phone the instant signal
/// came back — they are usually still driving, and a confirmation that
/// vanishes before it is read is a confirmation that never happened. That
/// matters most in exactly the case where the message is not "all quiet":
/// if a contact *was* woken while the traveller was dark, this screen is
/// the only place they find that out, and it must not be possible to miss.
///
/// [trip] must be a snapshot taken *before* reconnection — once a trip
/// closes (EXITED/RESOLVED), the backend no longer returns it from
/// /travellers/me/trip (that endpoint only looks at non-terminal trips —
/// see state_machine.py's active_trip_for), so reading trip state fresh
/// at this screen would just see null and always claim "never notified,"
/// regardless of what actually happened. See HomeShell's
/// _reconnectionTrip for where the snapshot is taken.
class ReconnectionScreen extends StatelessWidget {
  final Trip? trip;
  final VoidCallback? onDismiss;

  const ReconnectionScreen({super.key, this.trip, this.onDismiss});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final notes = trip?.notifications ?? const [];
    final humanNotified = notes.any((n) => n != 'tier0');

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // Center — the outer Column is left-aligned (for the text
            // below), and a fixed-size widget like this circle shrink-wraps
            // and sticks to the left edge under that alignment instead of
            // centering, the same issue fixed in onboarding_screen.dart.
            Center(
              child: TweenAnimationBuilder<double>(
                tween: Tween(begin: 0, end: 1),
                duration: const Duration(milliseconds: 500),
                curve: Curves.easeOutBack,
                builder: (context, v, child) =>
                    Transform.scale(scale: v, child: child),
                child: Container(
                  width: 72,
                  height: 72,
                  decoration: BoxDecoration(
                    color: (humanNotified ? c.amber : c.accent)
                        .withValues(alpha: 0.15),
                    shape: BoxShape.circle,
                  ),
                  child: Icon(
                    humanNotified
                        ? Icons.notifications_active_rounded
                        : Icons.check_rounded,
                    color: humanNotified ? c.amber : c.accent,
                    size: 36,
                  ),
                ),
              ),
            ),
            const SizedBox(height: 24),
            Text(
              'Welcome back online.',
              style: Theme.of(context).textTheme.headlineMedium,
            ),
            const SizedBox(height: 8),
            Text(
              _summary(trip),
              style: TextStyle(color: c.textSecondary, height: 1.5),
            ),
            const SizedBox(height: 28),
            // A traveller whose contact was woken has something to do about
            // it, so the button says so rather than just "OK".
            ElevatedButton(
              onPressed: onDismiss,
              child: Text(humanNotified ? 'Got it — I\'ll let them know' : 'Done'),
            ),
          ],
        ),
      ),
    );
  }

  String _summary(Trip? t) {
    final notes = t?.notifications ?? const [];
    if (notes.contains('tier2')) {
      return "Trip completed. Your contact and the emergency centre were "
          "notified before you reconnected — worth letting them know "
          "you're safe.";
    }
    if (notes.contains('tier1')) {
      return "Trip completed. Your contact was notified before you "
          "reconnected — worth letting them know you're safe.";
    }
    if (notes.contains('tier0')) {
      // Tier 0 fired and was answered, or the traveller reconnected during
      // it. Worth saying explicitly: the window was too tight, and the
      // system came to them rather than to anyone else.
      return "Trip completed. You ran past the expected time, so we checked "
          "with you directly — nobody else was contacted.";
    }
    return 'Trip completed safely. Your contact was never notified.';
  }
}

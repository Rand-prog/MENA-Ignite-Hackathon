import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// Shared chrome for a single onboarding step: progress dots, title,
/// body, and a primary action pinned to the bottom. Keeps every step
/// visually identical so the four-step flow reads as one continuous
/// screen rather than four different ones.
class StepScaffold extends StatelessWidget {
  final int stepIndex; // 0-based
  final int stepCount;
  final String title;
  final String? subtitle;
  final Widget body;
  final String primaryLabel;
  final VoidCallback? onPrimary;
  final bool primaryEnabled;
  final Widget? secondary;
  final VoidCallback? onBack;

  const StepScaffold({
    super.key,
    required this.stepIndex,
    required this.stepCount,
    required this.title,
    this.subtitle,
    required this.body,
    required this.primaryLabel,
    required this.onPrimary,
    this.primaryEnabled = true,
    this.secondary,
    this.onBack,
  });

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 20, 24, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (onBack != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  children: [
                    IconButton(
                      onPressed: onBack,
                      icon: const Icon(Icons.arrow_back_rounded),
                      color: c.textSecondary,
                      visualDensity: VisualDensity.compact,
                      padding: EdgeInsets.zero,
                      constraints: const BoxConstraints(
                        minWidth: 40,
                        minHeight: 40,
                      ),
                    ),
                  ],
                ),
              ),
            Semantics(
              label: 'Step ${stepIndex + 1} of $stepCount',
              excludeSemantics: true,
              child: Row(
              children: List.generate(stepCount, (i) {
                final active = i <= stepIndex;
                return Expanded(
                  child: Container(
                    height: 4,
                    margin: EdgeInsets.only(right: i == stepCount - 1 ? 0 : 6),
                    decoration: BoxDecoration(
                      color: active ? c.accent : c.border,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                );
              }),
            )),
            const SizedBox(height: 28),
            Semantics(
              header: true,
              child: Text(title, style: Theme.of(context).textTheme.headlineMedium),
            ),
            if (subtitle != null) ...[
              const SizedBox(height: 8),
              Text(subtitle!, style: Theme.of(context).textTheme.bodyLarge
                  ?.copyWith(color: c.textSecondary)),
            ],
            const SizedBox(height: 28),
            Expanded(child: body),
            if (secondary != null) ...[secondary!, const SizedBox(height: 12)],
            ElevatedButton(
              onPressed: primaryEnabled ? onPrimary : null,
              child: Text(primaryLabel),
            ),
          ],
        ),
      ),
    );
  }
}

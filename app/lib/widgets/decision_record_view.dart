import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// The agent's reasoning, shown to the traveller.
///
/// The dashboard has always rendered this for a dispatcher. The person the
/// decision is actually *about* — whose window it is, whose contact gets
/// woken — could not see any of it. They got a number ("115 min") with no
/// account of where it came from, which is the shape of an arbitrary rule
/// rather than a judgement.
///
/// For a product whose pitch is an agent making a risk call, showing that
/// call to the person it lands on is not a nice-to-have; it is the
/// difference between "the app decided" and "here is why". The field was
/// already in the payload — this only renders it.
///
/// Parses the same plain-text "key   value" format the backend writes (see
/// backend/app/agent/decision_record.py) and shows the lines a traveller
/// can act on. Deliberately not all of them: `tools` is a list of CAMARA
/// call names that means something to a dispatcher and nothing to a driver.
class DecisionRecordView extends StatelessWidget {
  final String record;
  const DecisionRecordView({super.key, required this.record});

  /// Keys worth showing a traveller, in the order they answer the question
  /// "why this long?".
  static const _shown = {
    'reasoning': 'Why',
    'history': 'Past crossings here',
    'signals': 'What it looked at',
    'window': 'Window set',
    'declared': 'Your declared stop',
    'model': 'Decided by',
  };

  static List<({String key, String value})> parse(String text) {
    final rows = <({String key, String value})>[];
    for (final raw in text.split('\n')) {
      if (raw.trim().isEmpty) continue;
      final m = RegExp(r'^(\S+)\s+(.*)$').firstMatch(raw);
      if (m != null) {
        rows.add((key: m.group(1)!, value: m.group(2)!.trim()));
      } else if (rows.isNotEmpty) {
        final last = rows.removeLast();
        rows.add((key: last.key, value: '${last.value} ${raw.trim()}'));
      }
    }
    return rows;
  }

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final rows = parse(record).where((r) => _shown.containsKey(r.key)).toList();
    if (rows.isEmpty) return const SizedBox.shrink();

    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: c.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: c.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final r in rows) ...[
            Semantics(
              label: '${_shown[r.key]}: ${_humanise(r.key, r.value)}',
              excludeSemantics: true,
              child: Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      _shown[r.key]!.toUpperCase(),
                      style: TextStyle(
                        color: c.textMuted,
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 0.6,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      _humanise(r.key, r.value),
                      style: TextStyle(
                        color: r.key == 'reasoning'
                            ? c.textPrimary
                            : c.textSecondary,
                        fontSize: r.key == 'reasoning' ? 14 : 13,
                        height: 1.45,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  /// Turn the dispatcher-facing shorthand into something a driver reads
  /// without a legend. `signals` is written as `congestion=heavy
  /// battery=18% hour=13 zone=104km`, which is fine on a console and
  /// hostile on a phone.
  static String _humanise(String key, String value) {
    if (key != 'signals') return value;
    final parts = value.split(RegExp(r'\s{2,}')).where((s) => s.trim().isNotEmpty);
    final out = <String>[];
    for (final p in parts) {
      final kv = p.split('=');
      if (kv.length != 2) {
        out.add(p);
        continue;
      }
      final v = kv[1];
      out.add(switch (kv[0]) {
        'congestion' => '$v traffic',
        'battery' => '$v battery at entry',
        'hour' => 'entered around ${v.padLeft(2, '0')}:00',
        'zone' => '$v corridor',
        _ => p,
      });
    }
    return out.join(' · ');
  }
}

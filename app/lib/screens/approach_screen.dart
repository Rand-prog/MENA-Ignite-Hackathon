import 'package:flutter/material.dart';

import '../models/trip.dart';
import '../models/zone.dart';
import '../theme/app_theme.dart';
import '../widgets/decision_record_view.dart';
import '../widgets/tick.dart';

/// Formats a duration the way a person would say it out loud.
String formatLeft(Duration d) {
  if (d.inSeconds <= 0) return 'now';
  if (d.inMinutes < 1) return '${d.inSeconds}s';
  if (d.inMinutes < 60) return '${d.inMinutes} min';
  final h = d.inHours;
  final m = d.inMinutes % 60;
  return m == 0 ? '${h}h' : '${h}h ${m}m';
}

String formatClock(DateTime t) {
  final h = t.hour.toString().padLeft(2, '0');
  final m = t.minute.toString().padLeft(2, '0');
  return '$h:$m';
}

/// The emergency contact by name where the app knows it, "your contact"
/// where it does not.
///
/// The name has been in StorageService since onboarding and, until now, no
/// screen ever read it — every sentence in the app said "your contact"
/// about a person the traveller had picked out by name. trip.dart's own
/// comment states the sentence this is for: "we'd text Omar in 42 minutes"
/// is a fact about a person; "we'd text your contact" is a category.
///
/// [contactLabelCapitalised] is the sentence-initial form — a real name is
/// already capitalised, the fallback phrase is not.
///
/// Callers must keep the label out of subject–verb agreement: two saved
/// contacts render as "Omar and Layla", which is plural, so "$name was
/// notified" breaks where "we notified $name" does not.
String contactLabel(String? name) => name ?? 'your contact';

String contactLabelCapitalised(String? name) => name ?? 'Your contact';

/// Screen 2 — Approach notification. Also doubles as the app's quiet idle
/// home: "nothing nearby" is the ~all-the-time state per docs/SignalGuard_
/// User_Flow Phase 1 ("the app is invisible"). BUFFER = "preparing you
/// now…"; ACTIVE = "you're offline-ready" with the risk-adjusted window;
/// TIER0_CHECKING = the one moment this app asks for something back.
class ApproachScreen extends StatelessWidget {
  final Trip? trip;
  final bool backendUnreachable;
  final String? backendUrl;
  final ThemeMode themeMode;
  final VoidCallback? onCycleTheme;
  final Future<void> Function(int minutes)? onDeclareStop;
  final Future<void> Function()? onAnswerTier0;

  /// The corridor this install actually has cached, so the idle card can
  /// name it. Optional and null-tolerant for the same reason as [tick].
  final Zone? zone;

  /// The emergency contact's own name — see [contactLabel]. Optional:
  /// null falls every sentence back to "your contact", which is what every
  /// existing caller and test gets.
  final String? contactName;

  /// Ticks once a second so the countdown sites below can rebuild
  /// themselves without the whole screen rebuilding with them.
  ///
  /// Optional: null (the default, and what every existing test above
  /// constructs) just means the countdown text renders once, from
  /// whatever `trip` says at build time, with no live re-render between
  /// parent rebuilds — exactly the old behaviour for anything that isn't
  /// HomeShell. HomeShell passes its own `ValueNotifier<int>` here; see
  /// its own comment on why the old shell-wide setState was the actual bug.
  final Listenable? tick;

  const ApproachScreen({
    super.key,
    this.trip,
    this.backendUnreachable = false,
    this.backendUrl,
    this.themeMode = ThemeMode.dark,
    this.onCycleTheme,
    this.onDeclareStop,
    this.onAnswerTier0,
    this.tick,
    this.zone,
    this.contactName,
  });

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final t = trip;
    final preparing = t != null && t.state == 'BUFFER';
    final ready = t != null && t.state == 'ACTIVE';
    final tier0 = t != null && t.isTier0;

    // Centre the card while it fits, scroll once it does not.
    //
    // The layout used to be a fixed Spacer/content/Spacer sandwich, which
    // is fine for a short card and silently clips a tall one. The
    // expandable reasoning made that reachable in practice — and on a
    // small handset the thing that gets cut off is the bottom of the
    // card, which is where the actions live. Caught by a widget test
    // rather than by looking at it on one comfortable screen size.
    return SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          return SingleChildScrollView(
            child: ConstrainedBox(
              constraints: BoxConstraints(minHeight: constraints.maxHeight),
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.podcasts_rounded, color: c.accent, size: 22),
                        const SizedBox(width: 8),
                        // Expanded rather than a fixed Text plus a Spacer:
                        // both hold the theme button against the right
                        // edge, but only this one gives the wordmark a
                        // bounded width. With the Spacer the row overflowed
                        // to the right at 375px once the type got large
                        // enough — a large-text accessibility setting is
                        // exactly that case.
                        Expanded(
                          child: Text(
                            'SignalGuard',
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                        ),
                        ThemeButton(mode: themeMode, onPressed: onCycleTheme),
                      ],
                    ),
                    if (backendUnreachable) ...[
                      const SizedBox(height: 16),
                      _BackendUnreachableBanner(backendUrl: backendUrl),
                    ],
                    const SizedBox(height: 48),
                    AnimatedSwitcher(
                      duration: const Duration(milliseconds: 350),
                      child: tier0
                          ? _Tier0Card(
                              trip: t,
                              onAnswer: onAnswerTier0,
                              onDeclareStop: onDeclareStop,
                              tick: tick,
                              contactName: contactName,
                            )
                          : ready
                              ? _ReadyCard(
                                  trip: t,
                                  onDeclareStop: onDeclareStop,
                                  tick: tick,
                                  contactName: contactName,
                                )
                              : preparing
                                  ? _PreparingCard(trip: t)
                                  : _IdleCard(
                                      onDeclareStop: onDeclareStop,
                                      zone: zone,
                                      contactName: contactName,
                                    ),
                    ),
                    const SizedBox(height: 32),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

/// Night / daylight / auto, cycled in place.
///
/// Public, and deliberately so: this used to be private to this screen,
/// which meant the control vanished the moment HomeShell swapped in
/// OfflineMapScreen. app_theme.dart's own comment names the offline map —
/// "the one that matters when everything else has failed" — as the screen
/// worst hurt by a dark-only palette, and it was the one screen with no way
/// to leave it.
class ThemeButton extends StatelessWidget {
  final ThemeMode mode;
  final VoidCallback? onPressed;
  const ThemeButton({super.key, required this.mode, this.onPressed});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final (icon, label) = switch (mode) {
      ThemeMode.dark => (Icons.dark_mode_outlined, 'Night'),
      ThemeMode.light => (Icons.light_mode_outlined, 'Daylight'),
      ThemeMode.system => (Icons.brightness_auto_outlined, 'Auto'),
    };
    return TextButton.icon(
      onPressed: onPressed,
      icon: Icon(icon, size: 17, color: c.textSecondary),
      label: Text(label, style: TextStyle(color: c.textSecondary, fontSize: 13)),
      // 48px tall, not shrink-wrapped to its 17px icon and 13px text. See
      // _PlannedStopButton's style for why every driver-facing control in
      // this file now sets a real minimum.
      style: TextButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12),
        minimumSize: const Size(0, 48),
      ),
    );
  }
}

// -- idle ---------------------------------------------------------------------

class _IdleCard extends StatelessWidget {
  final Future<void> Function(int)? onDeclareStop;
  final Zone? zone;
  final String? contactName;
  const _IdleCard({this.onDeclareStop, this.zone, this.contactName});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    final z = zone;
    final name = contactName;
    return Column(
      key: const ValueKey('idle'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Container(
          width: 64,
          height: 64,
          decoration: BoxDecoration(
            color: c.surface,
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: c.border),
          ),
          child: Icon(Icons.shield_moon_outlined, color: c.accentDim, size: 30),
        ),
        const SizedBox(height: 20),
        Semantics(
          header: true,
          child: Text(
            'Watching, quietly.',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'No dead zone nearby. You don\'t need to do anything — close '
          'the app and go.',
          style: TextStyle(color: c.textSecondary, height: 1.5),
        ),
        // The one piece of evidence that this install is actually set up.
        //
        // Without it, a correctly configured phone and one whose zone fetch
        // came back empty render the identical shield-and-"Watching,
        // quietly" screen — and this screen's whole job is to earn the
        // trust behind "close the app and never open it again".
        //
        // It states CONFIGURATION and nothing else. Detection is
        // network-side, so "armed for Highway 15" is a fact about what is
        // switched on; anything hinting at where the traveller is right now
        // would be the app claiming a thing it does not have. Only the
        // first zone is cached (onboarding_screen.dart's `zones.first`), so
        // the wording names that corridor rather than implying it is the
        // whole of the coverage.
        if (z != null && name != null) ...[
          const SizedBox(height: 12),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(Icons.check_circle_outline_rounded,
                  size: 15, color: c.accentDim),
              const SizedBox(width: 7),
              // Expanded, because the contact name is free text from
              // onboarding — a long one has to wrap here, not overflow.
              Expanded(
                child: Text(
                  'Armed for ${z.label} · if you go quiet in there, we '
                  'text $name.',
                  style: TextStyle(
                    color: c.textMuted,
                    fontSize: 13,
                    height: 1.45,
                  ),
                ),
              ),
            ],
          ),
        ],
        const SizedBox(height: 24),
        // Declaring the stop *before* setting off is the cheap case: no
        // alarm to cancel, no contact to reassure afterwards.
        _PlannedStopButton(
          onDeclareStop: onDeclareStop,
          label: 'Planning to stop on the way?',
        ),
      ],
    );
  }
}

// -- preparing ----------------------------------------------------------------

/// The approach card, with the offline-map preparation shown as a
/// progress bar.
///
/// History, because it matters and will otherwise be re-litigated: this
/// card originally claimed to be "downloading the offline map" under an
/// indeterminate bar. That was removed as dishonest — the corridor tiles
/// ship inside the APK (`assets/map/`), nothing is fetched, and a bar that
/// tracks nothing is theatre in a product whose credibility rests on being
/// straight about what it knows. A test pinned the removal.
///
/// It is back deliberately, for demo use, and the wording is the part that
/// was fixed rather than reverted: the bar now says the map is being
/// *prepared for this corridor*, which is a thing that is genuinely
/// happening in this window — the agent is reading congestion, battery and
/// zone profile and setting the monitoring window — rather than
/// "downloading", which is a thing that is not. The bar is still a
/// timer, not a measurement of that work, and [demoProgress] is what
/// drives it.
class _PreparingCard extends StatefulWidget {
  final Trip? trip;
  const _PreparingCard({this.trip});

  @override
  State<_PreparingCard> createState() => _PreparingCardState();
}

class _PreparingCardState extends State<_PreparingCard>
    with SingleTickerProviderStateMixin {
  late final AnimationController _bar;

  @override
  void initState() {
    super.initState();
    // Runs slightly longer than the backend's demo hold so the bar is
    // still moving when the state flips to ACTIVE, rather than sitting
    // full and visibly waiting.
    _bar = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 14),
    )..forward();
  }

  @override
  void dispose() {
    _bar.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return Column(
      key: const ValueKey('preparing'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _PulsingDot(color: c.amber),
        const SizedBox(height: 20),
        Semantics(
          header: true,
          child: Text(
            'Low-coverage zone ahead.',
            style: Theme.of(context)
                .textTheme
                .headlineMedium
                ?.copyWith(color: c.amber),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'Getting the offline map ready for this corridor, reading the '
          'network signals, and setting how long you have before anyone '
          'is told.',
          style: TextStyle(color: c.textSecondary, height: 1.5),
        ),
        const SizedBox(height: 22),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: AnimatedBuilder(
            animation: _bar,
            builder: (context, _) => LinearProgressIndicator(
              // Eased so it moves quickly at first and crawls at the end,
              // which is what a real transfer looks like.
              value: Curves.easeOutCubic.transform(_bar.value) * 0.97,
              minHeight: 6,
              backgroundColor: c.surfaceRaised,
              valueColor: AlwaysStoppedAnimation<Color>(c.amber),
            ),
          ),
        ),
        const SizedBox(height: 10),
        AnimatedBuilder(
          animation: _bar,
          builder: (context, _) => Text(
            'Offline map · '
            '${(Curves.easeOutCubic.transform(_bar.value) * 97).round()}%',
            style: TextStyle(
              color: c.textMuted,
              fontSize: 12.5,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ),
      ],
    );
  }
}

// -- ready --------------------------------------------------------------------

class _ReadyCard extends StatefulWidget {
  final Trip trip;
  final Future<void> Function(int)? onDeclareStop;
  final Listenable? tick;
  final String? contactName;
  const _ReadyCard({
    required this.trip,
    this.onDeclareStop,
    this.tick,
    this.contactName,
  });

  @override
  State<_ReadyCard> createState() => _ReadyCardState();
}

class _ReadyCardState extends State<_ReadyCard> {
  /// Collapsed by default. The idle state of this app is "you do not need
  /// to look at me", and opening on a wall of reasoning would contradict
  /// that — but a traveller who wants to know why their window is 115
  /// minutes should not have to ask a dispatcher.
  bool _showWhy = false;

  @override
  Widget build(BuildContext context) {
    final trip = widget.trip;
    final onDeclareStop = widget.onDeclareStop;
    final c = AppPalette.of(context);
    final risk = trip.risk ?? 'LOW';
    final isLow = risk == 'LOW';

    return Column(
      key: const ValueKey('ready'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.offline_pin_rounded, color: c.accent, size: 40),
        const SizedBox(height: 16),
        Semantics(
          header: true,
          child: Text(
            'You\'re offline-ready.',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
        ),
        const SizedBox(height: 14),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            _Chip(label: 'Risk: $risk', color: isLow ? c.accent : c.amber),
            if (trip.predictedCrossingMin != null)
              _Chip(
                label: '~${trip.predictedCrossingMin} min',
                color: c.textSecondary,
              ),
            if (trip.plannedStopMin > 0)
              _Chip(
                label: '+${trip.plannedStopMin} min stop',
                color: c.amber,
              ),
          ],
        ),
        const SizedBox(height: 16),
        // The one sentence that makes this product legible. "115 min
        // window" is arithmetic the traveller has to do while driving;
        // "we'd text Omar at 15:40" is a fact they can act on.
        //
        // This is the countdown site — it used to rebuild because the
        // whole shell rebuilt every second (see HomeShell's _tick
        // comment). Now only this block re-renders, driven by widget.tick;
        // trip.contactAlertAt/timeUntilContactAlerted are recomputed fresh
        // on every tick rather than once at the top of build(), which is
        // the only way this stays correct without the tick.
        Tick(tick: widget.tick, builder: (_) => _deadlineBlock(c)),
        if (trip.decisionRecord != null) ...[
          const SizedBox(height: 14),
          _WhyToggle(
            expanded: _showWhy,
            onTap: () => setState(() => _showWhy = !_showWhy),
          ),
          // AnimatedSize over a conditional child, not AnimatedCrossFade:
          // the latter builds BOTH children and merely sizes one to zero,
          // which leaves the collapsed reasoning sitting in the semantics
          // tree. A screen reader would then read out the explanation the
          // sighted user has explicitly not asked for — the disclosure
          // would be visual only, which is not a disclosure.
          AnimatedSize(
            duration: const Duration(milliseconds: 220),
            curve: Curves.easeOut,
            alignment: Alignment.topLeft,
            child: _showWhy
                ? Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: DecisionRecordView(record: trip.decisionRecord!),
                  )
                : const SizedBox(width: double.infinity),
          ),
        ],
        const SizedBox(height: 20),
        _PlannedStopButton(
          onDeclareStop: onDeclareStop,
          label: 'Stopping for a while? Push the deadline back',
        ),
      ],
    );
  }

  Widget _deadlineBlock(AppPalette c) {
    final alertAt = widget.trip.contactAlertAt;
    final left = widget.trip.timeUntilContactAlerted;
    final who = contactLabel(widget.contactName);
    if (alertAt != null && left != null) {
      return _DeadlineLine(
        headline: 'If you\'re not back by ${formatClock(alertAt)}, '
            'we text $who.',
        sub: 'That\'s ${formatLeft(left)} from now. Until then, nobody '
            'hears anything.',
      );
    }
    return Text(
      '${contactLabelCapitalised(widget.contactName)} will be alerted only '
      'if you don\'t reconnect in time.',
      style: TextStyle(color: c.textSecondary, height: 1.5),
    );
  }
}

class _WhyToggle extends StatelessWidget {
  final bool expanded;
  final VoidCallback onTap;
  const _WhyToggle({required this.expanded, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return Semantics(
      button: true,
      expanded: expanded,
      label: expanded
          ? 'Hide why this window was chosen'
          : 'Show why this window was chosen',
      excludeSemantics: true,
      child: TextButton.icon(
        onPressed: onTap,
        icon: Icon(
          expanded ? Icons.expand_less_rounded : Icons.expand_more_rounded,
          size: 18,
          color: c.textSecondary,
        ),
        label: Text(
          expanded ? 'Hide the reasoning' : 'Why this long?',
          style: TextStyle(color: c.textSecondary, fontSize: 13),
        ),
        // Same 48px minimum as the other inline actions in this file — see
        // _PlannedStopButton. Left-aligned so it still reads as a link.
        style: TextButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12),
          alignment: Alignment.centerLeft,
          minimumSize: const Size(0, 48),
        ),
      ),
    );
  }
}

class _DeadlineLine extends StatelessWidget {
  final String headline;
  final String sub;
  const _DeadlineLine({required this.headline, required this.sub});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    // One announcement for the whole block, and a live region: this is the
    // single most important fact on the screen and it changes every
    // second, so a screen reader must read it as one sentence rather than
    // as two fragments re-read on every tick.
    return Semantics(
      liveRegion: true,
      label: '$headline $sub',
      excludeSemantics: true,
      child: _body(c),
    );
  }

  Widget _body(AppPalette c) {
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
          Text(
            headline,
            style: TextStyle(
              color: c.textPrimary,
              fontSize: 15,
              fontWeight: FontWeight.w600,
              height: 1.4,
            ),
          ),
          const SizedBox(height: 6),
          Text(sub, style: TextStyle(color: c.textSecondary, height: 1.45, fontSize: 13)),
        ],
      ),
    );
  }
}

// -- Tier 0 -------------------------------------------------------------------

/// The only screen in this app that asks the traveller for something.
///
/// The window has expired and the handset is back in signal. Before any
/// human is told anything, the system asks the person themselves. Answering
/// closes the trip with nobody contacted; not answering costs 90 seconds
/// and then does exactly what it would have done anyway.
class _Tier0Card extends StatefulWidget {
  final Trip trip;
  final Future<void> Function()? onAnswer;
  final Future<void> Function(int)? onDeclareStop;
  final Listenable? tick;
  final String? contactName;
  const _Tier0Card({
    required this.trip,
    this.onAnswer,
    this.onDeclareStop,
    this.tick,
    this.contactName,
  });

  @override
  State<_Tier0Card> createState() => _Tier0CardState();
}

class _Tier0CardState extends State<_Tier0Card> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);

    return Column(
      key: const ValueKey('tier0'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _PulsingDot(color: c.amber),
        const SizedBox(height: 20),
        Semantics(
          header: true,
          child: Text(
            'Still there?',
            style: Theme.of(context)
                .textTheme
                .headlineMedium
                ?.copyWith(color: c.amber),
          ),
        ),
        const SizedBox(height: 8),
        Text(
          'You\'re past the time we expected you back. Nobody has been '
          'contacted yet — tell us you\'re fine and nobody will be.',
          style: TextStyle(color: c.textSecondary, height: 1.5),
        ),
        // Countdown site — 90 seconds is short enough that a stale value
        // here would actually mislead, not just look sluggish. Rebuilds
        // from widget.tick alone, not the whole card.
        Tick(tick: widget.tick, builder: (_) => _tier0Countdown(c)),
        const SizedBox(height: 20),
        ElevatedButton(
          onPressed: _busy ? null : _answer,
          child: Text(_busy ? 'Sending…' : "I'm fine"),
        ),
        const SizedBox(height: 10),
        _PlannedStopButton(
          onDeclareStop: widget.onDeclareStop,
          label: 'Still going — I just need longer',
        ),
      ],
    );
  }

  Widget _tier0Countdown(AppPalette c) {
    final left = widget.trip.timeUntilTier0Expires;
    if (left == null) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Semantics(
        liveRegion: true,
        child: Text(
          left.inSeconds > 0
              ? 'We text ${contactLabel(widget.contactName)} in '
                  '${formatLeft(left)}.'
              : 'Contacting ${widget.contactName ?? 'your emergency contact'} '
                  'now.',
          style: TextStyle(
            color: c.amber,
            fontWeight: FontWeight.w600,
            fontSize: 14,
          ),
        ),
      ),
    );
  }

  Future<void> _answer() async {
    setState(() => _busy = true);
    try {
      await widget.onAnswer?.call();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

// -- planned stop -------------------------------------------------------------

class _PlannedStopButton extends StatelessWidget {
  final Future<void> Function(int)? onDeclareStop;
  final String label;
  const _PlannedStopButton({this.onDeclareStop, required this.label});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    if (onDeclareStop == null) return const SizedBox.shrink();
    return TextButton.icon(
      onPressed: () => _open(context),
      icon: Icon(Icons.more_time_rounded, size: 18, color: c.textSecondary),
      label: Text(
        label,
        style: TextStyle(color: c.textSecondary, fontSize: 13),
      ),
      // 48px minimum height, and no shrinkWrap.
      //
      // This used to collapse to its content — an 18px icon and 13px text,
      // roughly a 30px target. It is aimed at a driver: a phone in a
      // windshield mount on a rough road, one thumb, eyes on the road. And
      // it is the one routine thing this product ever asks of a moving
      // traveller — declaring a stop is the mechanism that prevents the
      // false alarms that erode a contact's trust in the whole system. A
      // target that gets missed there is not a style nit.
      //
      // The theme already sets a 52px minimum for Elevated and Outlined
      // buttons; these inline ones were the only controls opting out.
      // alignment stays centerLeft so they still read as links, not
      // buttons.
      style: TextButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12),
        alignment: Alignment.centerLeft,
        minimumSize: const Size(0, 48),
      ),
    );
  }

  Future<void> _open(BuildContext context) async {
    final minutes = await showModalBottomSheet<int>(
      context: context,
      backgroundColor: AppPalette.of(context).bg,
      showDragHandle: true,
      builder: (ctx) => _PlannedStopSheet(),
    );
    if (minutes == null) return;
    try {
      await onDeclareStop!(minutes);
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              minutes == 0
                  ? 'Break cleared — back to the original window.'
                  : 'Got it. We\'ll wait an extra $minutes minutes before '
                      'telling anyone.',
            ),
          ),
        );
      }
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }
}

class _PlannedStopSheet extends StatelessWidget {
  static const _options = [15, 30, 60, 120];

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(24, 4, 24, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'How long are you stopping?',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            Text(
              'We\'ll hold off telling anyone for that much longer. Monitoring '
              'stays on the whole time — this moves the deadline, it doesn\'t '
              'switch anything off.',
              style: TextStyle(color: c.textSecondary, height: 1.45, fontSize: 13),
            ),
            const SizedBox(height: 20),
            Wrap(
              spacing: 10,
              runSpacing: 10,
              children: [
                for (final m in _options)
                  OutlinedButton(
                    onPressed: () => Navigator.pop(context, m),
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size(0, 46),
                      padding: const EdgeInsets.symmetric(horizontal: 20),
                    ),
                    child: Text('$m min'),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            TextButton(
              onPressed: () => Navigator.pop(context, 0),
              child: const Text('Cancel a break I already declared'),
            ),
          ],
        ),
      ),
    );
  }
}

// -- shared bits --------------------------------------------------------------

/// Shown only after repeated failed polls (see HomeShell._backendUnreachable)
/// — a wrong Backend URL or a down backend otherwise looks identical to
/// "no dead zone nearby, all quiet" on this screen, which is exactly the
/// silent failure this app can least afford during a demo.
class _BackendUnreachableBanner extends StatelessWidget {
  final String? backendUrl;
  const _BackendUnreachableBanner({this.backendUrl});

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: c.danger.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: c.danger.withValues(alpha: 0.35)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.wifi_off_rounded, color: c.danger, size: 18),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  "Can't reach SignalGuard",
                  style: TextStyle(
                    color: c.danger,
                    fontWeight: FontWeight.w600,
                    fontSize: 13,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  backendUrl != null
                      ? 'Trip status won\'t update until $backendUrl is reachable.'
                      : 'Trip status won\'t update until the backend is reachable.',
                  style: TextStyle(
                    color: c.textSecondary,
                    fontSize: 12,
                    height: 1.4,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
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

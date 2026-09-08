import 'package:flutter/widgets.dart';

/// Rebuilds [builder] on every tick of [tick] — or once, statically, when
/// it's null.
///
/// The shared plumbing behind every on-screen countdown in this app.
/// HomeShell used to drive its countdowns with a Timer.periodic that called
/// setState on the whole shell every second, which rebuilt ApproachScreen
/// or OfflineMapScreen and everything under them — MapViewport's
/// LayoutBuilder, image positioning and CustomPaint included — for the
/// entire duration of a crossing, in the one product whose risk model keys
/// off battery at entry. HomeShell now owns a single `ValueNotifier<int>`
/// that a Timer increments (no setState), and only the widgets built
/// through Tick below — the actual "38 min" / "we text your contact at
/// 15:40" text — listen to it and rebuild.
///
/// [tick] being null (the default in every screen's constructor) makes
/// this render [builder] once and never again, which is exactly right for
/// a widget test that pumps a single frame and never advances a timer.
class Tick extends StatelessWidget {
  final Listenable? tick;
  final WidgetBuilder builder;
  const Tick({super.key, required this.tick, required this.builder});

  @override
  Widget build(BuildContext context) {
    final listenable = tick;
    if (listenable == null) return builder(context);
    return AnimatedBuilder(
      animation: listenable,
      builder: (context, _) => builder(context),
    );
  }
}

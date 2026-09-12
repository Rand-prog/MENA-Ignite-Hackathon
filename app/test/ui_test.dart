// Tests for the traveller-facing UI added in the UX pass.
//
// These assert the semantics tree rather than pixels, for two reasons: it
// is the only thing a screen-reader user actually receives, and it is the
// part most easily broken by a later refactor that still "looks right".
//
// The widgets under test are all pure — they take a Trip and render — so
// none of this needs SharedPreferences, platform channels or a backend.
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:signalguard/models/trip.dart';
import 'package:signalguard/models/zone.dart';
import 'package:signalguard/screens/approach_screen.dart';
import 'package:signalguard/screens/offline_map_screen.dart';
import 'package:signalguard/services/location_service.dart';
import 'package:signalguard/theme/app_theme.dart';
import 'package:signalguard/widgets/decision_record_view.dart';

const _record = '''
signals   congestion=heavy  battery=18%  hour=13  zone=104km
model     gemini-flash-lite-latest
history   3 past crossings at this hour: typical 72 min, slowest 91 min
reasoning Low battery outweighs the benefit of slower speeds.
window    165 min (nominal 115 + buffer)
tools     get_zone_profile, get_congestion_insights, get_location
action    QoD warranted — session held for the reconnection edge
''';

Trip _trip({
  String state = 'ACTIVE',
  int windowMin = 165,
  Duration untilDeadline = const Duration(minutes: 90),
  List<String> notifications = const [],
  String? record = _record,
}) {
  final now = DateTime(2026, 9, 7, 14, 0);
  return Trip(
    tripId: 't1',
    state: state,
    receivedAt: DateTime.now(),
    risk: 'ELEVATED',
    predictedCrossingMin: 115,
    monitoringWindowMin: windowMin,
    decisionRecord: record,
    notifications: notifications,
    serverNow: now,
    windowDeadline: now.add(untilDeadline),
    tier0Deadline: now.add(untilDeadline + const Duration(seconds: 90)),
    congestionTier: 'heavy',
    batteryAtEntry: 18,
  );
}

const _zone = Zone(
  zoneId: 'JO-H15-MUDAWWARA',
  label: 'Highway 15 — Desert Highway, Al Mudawwara approach',
  entryLat: 29.832,
  entryLon: 35.991,
  exitLat: 29.335,
  exitLon: 36.024,
  gateRadiusM: 8000,
  corridorKm: 104,
  nominalCrossingMin: 75,
);

Widget _host(Widget child, {Brightness brightness = Brightness.dark}) {
  return MaterialApp(
    theme: buildAppTheme(brightness: brightness),
    home: Scaffold(body: child),
  );
}

void main() {
  group('decision record, shown to the traveller', () {
    testWidgets('renders only the lines a driver can act on', (tester) async {
      await tester.pumpWidget(_host(const DecisionRecordView(record: _record)));

      expect(find.text('WHY'), findsOneWidget);
      expect(find.text('PAST CROSSINGS HERE'), findsOneWidget);
      expect(find.text('WINDOW SET'), findsOneWidget);
      // `tools` is a list of CAMARA call names — meaningful to a
      // dispatcher, noise to a driver.
      expect(find.text('TOOLS'), findsNothing);
      expect(find.textContaining('get_congestion_insights'), findsNothing);
    });

    testWidgets('translates the signals shorthand into English', (tester) async {
      await tester.pumpWidget(_host(const DecisionRecordView(record: _record)));

      // Raw form is `congestion=heavy  battery=18%  hour=13  zone=104km`.
      expect(find.textContaining('heavy traffic'), findsOneWidget);
      expect(find.textContaining('18% battery at entry'), findsOneWidget);
      expect(find.textContaining('entered around 13:00'), findsOneWidget);
      expect(find.textContaining('congestion='), findsNothing);
    });

    testWidgets('each row is one semantics announcement, not two fragments',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(const DecisionRecordView(record: _record)));

      expect(
        find.bySemanticsLabel(RegExp(r'^Why: Low battery outweighs')),
        findsOneWidget,
      );
      handle.dispose();
    });

    testWidgets('an empty or unrecognised record renders nothing at all',
        (tester) async {
      await tester.pumpWidget(_host(const DecisionRecordView(record: 'garbage')));
      expect(find.byType(Container), findsNothing);
    });
  });

  group('approach screen', () {
    testWidgets('states the wall-clock moment a contact would be told',
        (tester) async {
      await tester.pumpWidget(_host(ApproachScreen(trip: _trip())));
      await tester.pump();

      // The number that makes the product legible — not "165 min window".
      expect(find.textContaining('we text your contact'), findsOneWidget);
      expect(find.textContaining('from now'), findsOneWidget);
    });

    testWidgets('the deadline is a single live-region announcement',
        (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(ApproachScreen(trip: _trip())));
      await tester.pump();

      final node = tester.getSemantics(
        find.bySemanticsLabel(RegExp(r'we text your contact')),
      );
      expect(node.flagsCollection.isLiveRegion, isTrue);
      handle.dispose();
    });

    testWidgets('the reasoning is collapsed until asked for', (tester) async {
      await tester.pumpWidget(_host(ApproachScreen(trip: _trip())));
      await tester.pump();

      expect(find.text('Why this long?'), findsOneWidget);
      expect(find.text('WHY'), findsNothing);

      await tester.tap(find.text('Why this long?'));
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.text('Hide the reasoning'), findsOneWidget);
      expect(find.text('WHY'), findsOneWidget);
    });

    testWidgets('every card heading is marked as a heading', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(const ApproachScreen()));
      await tester.pump();

      final node = tester.getSemantics(find.text('Watching, quietly.'));
      expect(node.flagsCollection.isHeader, isTrue);
      handle.dispose();
    });

    testWidgets('Tier 0 asks for something and says what it costs',
        (tester) async {
      await tester.pumpWidget(_host(
        ApproachScreen(
          trip: _trip(
            state: 'TIER0_CHECKING',
            untilDeadline: const Duration(seconds: -10),
            notifications: const ['tier0'],
          ),
          onAnswerTier0: () async {},
        ),
      ));
      await tester.pump();

      expect(find.text('Still there?'), findsOneWidget);
      expect(find.text("I'm fine"), findsOneWidget);
      expect(
        find.textContaining('Nobody has been contacted yet'),
        findsOneWidget,
      );
    });

    testWidgets('the Tier 0 countdown is a live region', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(
        ApproachScreen(
          trip: _trip(state: 'TIER0_CHECKING'),
          onAnswerTier0: () async {},
        ),
      ));
      await tester.pump();

      final node = tester.getSemantics(
        find.textContaining('We text your contact in'),
      );
      expect(node.flagsCollection.isLiveRegion, isTrue);
      handle.dispose();
    });

    // This test used to assert the opposite -- that the card carried no
    // progress bar and never said "downloading" -- because the bar had
    // been removed as dishonest: the corridor tiles ship inside the APK,
    // so nothing was ever being fetched.
    //
    // The bar is back by an explicit product decision, for demo use, and
    // what this test now pins is the compromise that made it defensible:
    // the card shows preparation progress, but the word "downloading" is
    // still not allowed anywhere in it, because that specific claim is
    // the one that is false.
    testWidgets('the preparing card shows progress without claiming a download',
        (tester) async {
      await tester.pumpWidget(_host(ApproachScreen(trip: _trip(state: 'BUFFER'))));
      await tester.pump();

      expect(find.byType(LinearProgressIndicator), findsOneWidget);
      expect(find.textContaining('offline map'), findsWidgets);
      expect(find.textContaining('downloading'), findsNothing);
      expect(find.textContaining('Downloading'), findsNothing);
    });
  });

  // The offline map is the whole app once the radio is dark, and the one
  // number on it that can go missing is the traveller's own position. What
  // it says while that number is missing is the difference between a wait
  // and a dead end.
  group('offline map with no position fix', () {
    testWidgets('says "Locating" only while a fix is genuinely pending',
        (tester) async {
      await tester.pumpWidget(
        _host(const OfflineMapScreen(zone: _zone, trip: null)),
      );
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.text('Locating…'), findsOneWidget);
      expect(
        find.textContaining('Your position works without signal'),
        findsOneWidget,
      );
    });

    testWidgets('names the switch the traveller can actually flip',
        (tester) async {
      await tester.pumpWidget(
        _host(const OfflineMapScreen(
          zone: _zone,
          locationFault: LocationFault.serviceDisabled,
        )),
      );
      await tester.pump(const Duration(milliseconds: 50));

      // The bug this replaced: "Locating…" forever, describing a wait that
      // would never end, with no mention of the toggle that ends it.
      expect(find.text('Locating…'), findsNothing);
      expect(find.text('Location is switched off on this phone'),
          findsOneWidget);
      // And it must stop promising a position it does not have.
      expect(
        find.textContaining('Your position works without signal'),
        findsNothing,
      );
      expect(
        find.textContaining('The map and the countdown above still work'),
        findsOneWidget,
      );
    });

    testWidgets('a revoked permission is not the same message as a toggle',
        (tester) async {
      await tester.pumpWidget(
        _host(const OfflineMapScreen(
          zone: _zone,
          locationFault: LocationFault.permissionDenied,
        )),
      );
      await tester.pump(const Duration(milliseconds: 50));

      expect(find.text('SignalGuard is not allowed to use location'),
          findsOneWidget);
    });
  });

  group('theme', () {
    testWidgets('daylight raises text contrast rather than tinting the dark one',
        (tester) async {
      await tester.pumpWidget(
        _host(const ApproachScreen(), brightness: Brightness.light),
      );
      await tester.pump();

      final ctx = tester.element(find.byType(ApproachScreen));
      final day = AppPalette.of(ctx);
      expect(day.isDay, isTrue);

      // Text on background must clear WCAG AA for body text (4.5:1); this
      // theme exists for a phone in a windshield mount at midday, so the
      // bar is the readable one, not the passing one.
      expect(_contrast(day.textPrimary, day.bg), greaterThan(12));
      expect(_contrast(day.textSecondary, day.bg), greaterThan(4.5));
      expect(_contrast(day.accent, day.bg), greaterThan(4.5));
      expect(_contrast(day.danger, day.bg), greaterThan(4.5));
      expect(_contrast(day.amber, day.bg), greaterThan(4.5));
    });

    testWidgets('night keeps its own contrast', (tester) async {
      await tester.pumpWidget(_host(const ApproachScreen()));
      await tester.pump();
      final night = AppPalette.of(tester.element(find.byType(ApproachScreen)));
      expect(night.isDay, isFalse);
      expect(_contrast(night.textPrimary, night.bg), greaterThan(10));
      expect(_contrast(night.textSecondary, night.bg), greaterThan(4.5));
    });
  });
}

/// WCAG relative-luminance contrast ratio.
double _contrast(Color a, Color b) {
  final la = _luminance(a);
  final lb = _luminance(b);
  final hi = la > lb ? la : lb;
  final lo = la > lb ? lb : la;
  return (hi + 0.05) / (lo + 0.05);
}

double _luminance(Color c) {
  double channel(double v) =>
      v <= 0.03928 ? v / 12.92 : math.pow((v + 0.055) / 1.055, 2.4).toDouble();
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
}

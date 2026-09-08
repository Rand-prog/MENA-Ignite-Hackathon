import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

import '../models/trip.dart';

/// The ongoing notification shown while a crossing is live.
///
/// The product's central instruction to the traveller is "close the app and
/// go". That is the right instruction — the whole design is network-side so
/// the phone doesn't have to do anything — but it asks for trust that
/// nothing on the device then repays. Once the app is closed there is no
/// evidence at all that anything is watching, which turns the product's
/// best feature into a leap of faith at exactly the moment the traveller is
/// driving into somewhere with no signal.
///
/// So while a trip is live, a low-priority ongoing notification says what
/// the system is doing and when it would act:
///
///     SignalGuard is watching
///     Contact alerted at 19:07 if you're not back · 1h 44m
///
/// It is the natural home for the countdown: glanceable without unlocking,
/// present when the app is not, and it survives the app being swept away
/// by the OS — which on a long drive is the normal case, not the edge one.
///
/// Deliberately silent and non-dismissible-by-swipe while ACTIVE (ongoing:
/// true), and deliberately NOT silent for Tier 0 — that is the one moment
/// this app genuinely needs the traveller's attention, and a silent
/// notification would waste the 90 seconds it has.
///
/// No web implementation exists for this plugin, so every entry point
/// no-ops under [kIsWeb] rather than throwing. The dev-time consequence is
/// that this specific behaviour cannot be verified in a browser preview —
/// it needs a real device or emulator.
class OngoingNotice {
  static const _channelId = 'signalguard_trip';
  static const _channelName = 'Active crossing';
  static const _channelDescription =
      'Shows that SignalGuard is monitoring a dead-zone crossing, and when '
      'your emergency contact would be told.';
  static const _notificationId = 1001;

  final FlutterLocalNotificationsPlugin _plugin =
      FlutterLocalNotificationsPlugin();

  bool _ready = false;
  String? _lastBody;
  bool _lastWasTier0 = false;

  Future<void> init() async {
    if (kIsWeb) return;
    const android = AndroidInitializationSettings('@mipmap/ic_launcher');
    const darwin = DarwinInitializationSettings(
      requestAlertPermission: false,
      requestBadgePermission: false,
      requestSoundPermission: false,
    );
    try {
      await _plugin.initialize(
        const InitializationSettings(android: android, iOS: darwin),
      );
      _ready = true;
    } catch (_) {
      // A platform without this plugin, or a denied channel, must never
      // take the app down over a notification. The screens are still the
      // source of truth; this is an addition to them.
      _ready = false;
    }
  }

  /// Ask for permission once, at onboarding, alongside the other grants —
  /// not at the moment a crossing starts, which is while driving.
  Future<void> requestPermission() async {
    if (kIsWeb || !_ready) return;
    try {
      await _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>()
          ?.requestNotificationsPermission();
    } catch (_) {
      // Optional grant. Declining costs the notification, nothing else.
    }
  }

  /// Reconcile the notification with the current trip. Safe to call on
  /// every poll: it only touches the OS when the visible text would
  /// actually change, so a 5-second poll doesn't re-post 12 notifications
  /// a minute.
  Future<void> sync(Trip? trip) async {
    if (kIsWeb || !_ready) return;

    if (trip == null || trip.isExited) {
      await clear();
      return;
    }

    final (title, body) = _copyFor(trip);
    final isTier0 = trip.isTier0;
    if (body == _lastBody && isTier0 == _lastWasTier0) return;
    _lastBody = body;
    _lastWasTier0 = isTier0;

    final android = AndroidNotificationDetails(
      _channelId,
      _channelName,
      channelDescription: _channelDescription,
      // Tier 0 is the single moment this app asks for something back, and
      // it has 90 seconds. Everything else is ambient and must not buzz.
      importance: isTier0 ? Importance.high : Importance.low,
      priority: isTier0 ? Priority.high : Priority.low,
      playSound: isTier0,
      enableVibration: isTier0,
      ongoing: !isTier0,
      autoCancel: false,
      showWhen: false,
      onlyAlertOnce: !isTier0,
      styleInformation: BigTextStyleInformation(body),
    );

    try {
      await _plugin.show(
        _notificationId,
        title,
        body,
        NotificationDetails(
          android: android,
          iOS: DarwinNotificationDetails(presentSound: isTier0),
        ),
      );
    } catch (_) {
      // See init() — never fatal.
    }
  }

  (String, String) _copyFor(Trip trip) {
    if (trip.isTier0) {
      final left = trip.timeUntilTier0Expires;
      return (
        'Still there?',
        left != null && left.inSeconds > 0
            ? 'Open SignalGuard and tap "I\'m fine" — otherwise your contact '
                'is told in ${_short(left)}.'
            : 'Contacting your emergency contact now.',
      );
    }
    if (trip.state == 'BUFFER') {
      return (
        'Preparing for a low-coverage zone',
        'Reading network signals and setting how long you have before '
            'anyone is told.',
      );
    }
    final left = trip.timeUntilContactAlerted;
    final at = trip.contactAlertAt;
    if (left == null || at == null) {
      return ('SignalGuard is watching', 'Monitoring your crossing.');
    }
    return (
      'SignalGuard is watching',
      'Your contact is told at ${_clock(at)} if you\'re not back — '
          '${_short(left)} from now.',
    );
  }

  Future<void> clear() async {
    if (kIsWeb || !_ready) return;
    if (_lastBody == null) return;
    _lastBody = null;
    _lastWasTier0 = false;
    try {
      await _plugin.cancel(_notificationId);
    } catch (_) {
      // See init().
    }
  }

  static String _short(Duration d) {
    if (d.inSeconds <= 0) return 'now';
    if (d.inMinutes < 1) return '${d.inSeconds}s';
    if (d.inMinutes < 60) return '${d.inMinutes} min';
    final h = d.inHours;
    final m = d.inMinutes % 60;
    return m == 0 ? '${h}h' : '${h}h ${m}m';
  }

  static String _clock(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';

import '../models/contact.dart';
import '../models/trip.dart';
import '../models/zone.dart';
import '../services/api_client.dart';
import '../services/battery_service.dart';
import '../services/location_service.dart';
import '../services/ongoing_notice.dart';
import '../services/storage_service.dart';
import 'approach_screen.dart';
import 'offline_map_screen.dart';
import 'onboarding_screen.dart';
import 'reconnection_screen.dart';

/// Owns the switch between screens 2/3/4 after onboarding, driven by real
/// connectivity state and the backend's trip status — never by a
/// hand-run demo timer. This is what the traveller actually sees; the
/// state machine underneath is entirely server-side (see
/// docs/SignalGuard_Technical_Feasibility.pdf §3).
class HomeShell extends StatefulWidget {
  final StorageService storage;
  final LocationService location;
  final ValueChanged<ThemeMode>? onThemeModeChanged;
  final ThemeMode themeMode;

  const HomeShell({
    super.key,
    required this.storage,
    required this.location,
    this.onThemeModeChanged,
    this.themeMode = ThemeMode.dark,
  });

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> with WidgetsBindingObserver {
  late final ApiClient _api;
  final _battery = BatteryService();
  final _notice = OngoingNotice();
  final _connectivity = Connectivity();

  Trip? _trip;
  Zone? _zone;
  bool _online = true;
  bool _showReconnection = false;
  Position? _position;

  /// Why there is no position, when there isn't one. The offline map is
  /// the only screen that can act on this, and it is the screen where it
  /// matters — see LocationService.LocationFault.
  LocationFault? _locationFault;

  /// The emergency contact's own name, for the screens that talk about
  /// them. See [_displayContactName]. Resolved once here rather than read
  /// per build: StorageService.contacts re-decodes JSON on every access,
  /// and the value cannot change without going back through onboarding.
  String? _contactName;

  // Drives the on-screen countdowns ("38 min until we text Omar") between
  // polls, without rebuilding anything else. This used to be a
  // Timer.periodic that called setState on the whole shell every second —
  // which rebuilds ApproachScreen or OfflineMapScreen and every child
  // underneath, MapViewport's LayoutBuilder/image positioning/CustomPaint
  // included, for the full duration of a crossing. Only the widgets built
  // through widgets/tick.dart's Tick listen to this and rebuild; see that
  // file's doc comment for the full picture.
  final ValueNotifier<int> _tick = ValueNotifier<int>(0);

  // Snapshot of _trip taken the instant reconnection is detected — once a
  // trip closes (EXITED/RESOLVED) the backend stops returning it from
  // /travellers/me/trip, so the very next poll (_poll() below, called
  // right after this) would otherwise null _trip out from under
  // ReconnectionScreen before it ever gets to show what actually
  // happened on this trip.
  Trip? _reconnectionTrip;

  // Two consecutive failed polls before surfacing anything — a single
  // dropped request is normal network noise, not worth alarming over.
  // Without this, a wrong Backend URL or a backend that's down looks
  // identical to "no dead zone nearby, all quiet" on screen — the one
  // silent failure mode that actually matters to catch, since the whole
  // app is built around silence meaning safety.
  int _pollFailures = 0;
  bool get _backendUnreachable => _pollFailures >= 2;

  Timer? _pollTimer;
  Timer? _batteryTimer;
  Timer? _tickTimer;
  StreamSubscription<List<ConnectivityResult>>? _connSub;
  StreamSubscription<Position>? _posSub;
  StreamSubscription<LocationFault?>? _faultSub;

  // The state that was on screen last frame, so a *transition* into a
  // crossing can fire haptics once instead of on every poll.
  String? _lastTripState;

  /// GPS mode last seen, so crossing the approach ring can trigger a fresh
  /// battery read exactly once.
  ///
  /// The risk model's single most important input is battery *at entry*,
  /// and it was being taken from whatever the last 60-second timer happened
  /// to report — potentially a reading from before the traveller set off.
  /// Sampling on the approach transition means the number the agent judges
  /// with is the number on the phone as it reaches the gate.
  GpsMode? _lastGpsMode;

  /// Poll cadence, chosen from what is actually at stake.
  ///
  /// This used to be a flat 5 seconds forever, including while
  /// backgrounded. Per the product's own docs the idle case — no trip, no
  /// zone nearby — is nearly all of the time, and each poll costs the
  /// handset a radio wake-up and the backend a full timer sweep. So: fast
  /// while something is actually happening, slow while nothing is, and
  /// stopped entirely when the app isn't foregrounded.
  static const _pollFast = Duration(seconds: 5);
  static const _pollIdle = Duration(seconds: 45);
  // Idle, but with somebody actually looking at the screen.
  //
  // The 45-second idle cadence above is justified by battery: a poll costs
  // a radio wake-up, and idle is nearly all of the time. But polling is
  // already stopped outright while the app is backgrounded, so the only
  // time _pollIdle is ever in force is when the app is open in front of a
  // person -- and there the battery argument is close to worthless while
  // the latency is not. A trip that opens is up to 45 seconds late to the
  // one screen the traveller has deliberately opened to look at, and the
  // BUFFER state it passes through lasts about three seconds, so the
  // approach card was effectively unreachable.
  static const _pollForeground = Duration(seconds: 4);
  Duration _currentPollInterval = _pollFast;
  // Starts true: this widget is only built once the app is on screen, and
  // didChangeAppLifecycleState does not fire for the state the app is
  // already in.
  bool _foregrounded = true;

  Duration get _wantedPollInterval {
    final t = _trip;
    if (t == null) return _foregrounded ? _pollForeground : _pollIdle;
    // A Tier 0 ping has a 90-second fuse; missing it because the app was
    // on a 45-second cadence would waste the entire point of the rung.
    if (t.isTier0) return const Duration(seconds: 3);
    if (t.isActive) return _pollFast;
    return _pollIdle;
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _api = ApiClient(widget.storage);
    _zone = widget.storage.zone;
    _contactName = _displayContactName(widget.storage.contacts);

    widget.location.startTracking();
    _posSub = widget.location.positions.listen((p) {
      if (!mounted) return;
      setState(() => _position = p);
      _retuneGps();
    });
    // The position stream only delivers *new* fixes — ask for the current
    // one immediately too, or the offline map sits on "Locating…" until
    // the next GPS update happens to fire, which can be a long wait.
    _faultSub = widget.location.faults.listen((f) {
      if (!mounted) return;
      setState(() => _locationFault = f);
    });
    widget.location.currentPosition().then((p) {
      if (mounted && p != null) {
        setState(() => _position = p);
        _retuneGps();
      }
    });

    _connSub = _connectivity.onConnectivityChanged.listen(_onConnectivity);
    _connectivity.checkConnectivity().then(_onConnectivity);

    _restartPolling(_pollFast);
    _batteryTimer = Timer.periodic(
      const Duration(seconds: 60),
      (_) => _reportBattery(),
    );
    // See _tick's own comment — this increments a ValueNotifier rather
    // than calling setState, so nothing rebuilds here except the actual
    // countdown text.
    _tickTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_trip != null) _tick.value++;
    });
    _poll();
    _reportBattery();
    _notice.init();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _batteryTimer?.cancel();
    _tickTimer?.cancel();
    _connSub?.cancel();
    _posSub?.cancel();
    _faultSub?.cancel();
    widget.location.stopTracking();
    _notice.clear();
    _tick.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Polling a backgrounded app spends the traveller's battery to update
    // a screen nobody is looking at. The trip's state lives server-side
    // regardless, so there is nothing to lose by stopping and catching up
    // on resume.
    if (state == AppLifecycleState.resumed) {
      _foregrounded = true;
      _poll();
      _restartPolling(_wantedPollInterval);
    } else if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      _foregrounded = false;
      _pollTimer?.cancel();
      _pollTimer = null;
    }
  }

  void _restartPolling(Duration interval) {
    _pollTimer?.cancel();
    _currentPollInterval = interval;
    _pollTimer = Timer.periodic(interval, (_) => _poll());
  }

  void _retunePolling() {
    final wanted = _wantedPollInterval;
    if (wanted != _currentPollInterval) _restartPolling(wanted);
  }

  void _retuneGps() {
    final mode =
        widget.location.modeFor(zone: _zone, pos: _position, online: _online);
    widget.location.setMode(mode);

    // Crossing into the approach ring is the last moment a fresh battery
    // reading can still reach the backend before the gate — see
    // _lastGpsMode. One extra report per approach, not a new timer.
    if (mode == GpsMode.approach && _lastGpsMode != GpsMode.approach) {
      _reportBattery();
    }
    _lastGpsMode = mode;
  }

  void _onConnectivity(List<ConnectivityResult> results) {
    final nowOnline = results.any((r) => r != ConnectivityResult.none);
    final wasOnline = _online;
    if (!mounted) return;
    setState(() => _online = nowOnline);
    _retuneGps();

    if (!nowOnline && wasOnline) {
      // Going dark is the moment the offline map becomes the only thing
      // the traveller has. A driver is not looking at the phone, so this
      // is announced physically rather than only visually.
      HapticFeedback.heavyImpact();
      SystemSound.play(SystemSoundType.alert);
    }

    if (nowOnline && !wasOnline) {
      // Regained signal — show the quiet confirmation. See
      // docs/SignalGuard_User_Flow Phase 4a. Snapshot _trip *before*
      // _poll() below can null it out (see _reconnectionTrip's own
      // comment) — this is what ReconnectionScreen actually reads.
      setState(() {
        _showReconnection = true;
        _reconnectionTrip = _trip;
      });
      HapticFeedback.lightImpact();
      _poll();
    }
  }

  Future<void> _poll() async {
    if (!_online || widget.storage.authToken == null) return;
    try {
      final trip = await _api.fetchCurrentTrip();
      if (!mounted) return;
      _onTripChanged(trip);
      setState(() {
        _trip = trip;
        _pollFailures = 0;
      });
      _retunePolling();
      // The ongoing notification is what makes "close the app and go"
      // trustworthy rather than a leap of faith — see ongoing_notice.dart.
      // sync() is cheap and self-deduplicating, so calling it per poll is
      // fine.
      _notice.sync(trip);
    } on ApiException catch (e) {
      if (e.isUnauthorized) {
        // Not a reachability problem at all — the backend answered fine
        // and said this token doesn't exist any more (e.g. its demo/dev
        // database was reset out from under an already-onboarded install,
        // exactly what happened testing this build). Showing the generic
        // "can't reach" banner here would be actively misleading — the
        // fix isn't a network fix, it's re-registering.
        await _handleUnauthorized();
        return;
      }
      if (mounted) setState(() => _pollFailures++);
    } catch (_) {
      // Offline mid-crossing is expected, not an error — the trip's own
      // state lives server-side regardless. But a *persistent* failure
      // while the device itself is online is a different situation (wrong
      // Backend URL, backend down) and worth surfacing.
      if (mounted) setState(() => _pollFailures++);
    }
  }

  /// Fire once on the transitions a driver needs to notice without
  /// looking at the screen.
  void _onTripChanged(Trip? trip) {
    final was = _lastTripState;
    final now = trip?.state;
    _lastTripState = now;
    if (now == was) return;

    if (was == null && now == 'BUFFER') {
      // Entered a monitored corridor.
      HapticFeedback.mediumImpact();
    } else if (now == 'TIER0_CHECKING') {
      // The window has expired and the system is about to tell somebody.
      // This is the one prompt in the app that genuinely needs answering,
      // and the traveller has 90 seconds.
      HapticFeedback.heavyImpact();
      SystemSound.play(SystemSoundType.alert);
    }
  }

  Future<void> _handleUnauthorized() async {
    await widget.storage.setOnboarded(false);
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(
        builder: (_) => OnboardingScreen(
          storage: widget.storage,
          location: widget.location,
          themeMode: widget.themeMode,
          onThemeModeChanged: widget.onThemeModeChanged,
        ),
      ),
      (route) => false,
    );
  }

  Future<void> _reportBattery() async {
    if (widget.storage.authToken == null) return;
    final level = await _battery.level();
    await _api.reportBattery(level);
  }

  Future<void> _declareStop(int minutes) async {
    await _api.declarePlannedStop(minutes);
    await _poll();
  }

  Future<void> _answerTier0() async {
    await _api.answerTier0('safe');
    await _notice.clear();
    await _poll();
  }

  /// "Omar", "Omar and Layla", or null when nothing usable was saved.
  ///
  /// The app has known this since onboarding and never once said it: every
  /// sentence in the UI read "your contact" about a person the traveller
  /// picked out by name, while trip.dart's own comment states the intended
  /// sentence — "we'd text Omar in 42 minutes."
  ///
  /// Two names read as a plural subject, which is why the copy in every
  /// screen that takes this keeps it out of subject-verb agreement.
  ///
  /// Truncated, because the contacts step is a free-text field with no
  /// length limit and this name is dropped mid-sentence into single lines
  /// on a 375px screen — every site wraps, but nothing wraps a pasted
  /// hundred-character run with no spaces in it.
  static String? _displayContactName(List<EmergencyContact> contacts) {
    String clip(String s) => s.length <= 24 ? s : '${s.substring(0, 23)}…';

    final names = contacts
        .map((c) => c.name.trim())
        .where((n) => n.isNotEmpty)
        .map(clip)
        .toList();
    if (names.isEmpty) return null;
    // Onboarding caps the list at two (_addContactField), so there is no
    // third case to fold in here.
    if (names.length == 1) return names.first;
    return '${names[0]} and ${names[1]}';
  }

  void _cycleTheme() {
    const order = [ThemeMode.dark, ThemeMode.light, ThemeMode.system];
    final next = order[(order.indexOf(widget.themeMode) + 1) % order.length];
    widget.onThemeModeChanged?.call(next);
  }

  @override
  Widget build(BuildContext context) {
    final Widget body;
    final String key;
    if (!_online) {
      body = OfflineMapScreen(
        zone: _zone,
        position: _position,
        locationFault: _locationFault,
        trip: _trip,
        tick: _tick,
        // The theme control has to come with this screen. Once the radio is
        // dark this is the whole app, and app_theme.dart names it as the
        // screen a near-black palette hurts most at midday.
        themeMode: widget.themeMode,
        onCycleTheme: _cycleTheme,
        contactName: _contactName,
      );
      key = 'offline';
    } else if (_showReconnection) {
      body = ReconnectionScreen(
        trip: _reconnectionTrip,
        contactName: _contactName,
        // Persist until acknowledged rather than auto-dismissing after
        // four seconds. A traveller who has just come back into signal is
        // usually still driving and looking at the road; a confirmation
        // that vanishes before it is read is a confirmation that never
        // happened.
        onDismiss: () => setState(() => _showReconnection = false),
      );
      key = 'recon';
    } else {
      body = ApproachScreen(
        trip: _trip,
        backendUnreachable: _backendUnreachable,
        backendUrl: widget.storage.backendUrl,
        themeMode: widget.themeMode,
        onCycleTheme: _cycleTheme,
        onDeclareStop: _declareStop,
        onAnswerTier0: _answerTier0,
        tick: _tick,
        // What the idle card needs to show evidence the install is armed
        // rather than just a shield and a reassuring sentence.
        zone: _zone,
        contactName: _contactName,
      );
      key = _trip?.isTier0 == true ? 'tier0' : 'approach';
    }

    return Scaffold(
      body: AnimatedSwitcher(
        duration: const Duration(milliseconds: 300),
        child: KeyedSubtree(key: ValueKey(key), child: body),
      ),
    );
  }
}

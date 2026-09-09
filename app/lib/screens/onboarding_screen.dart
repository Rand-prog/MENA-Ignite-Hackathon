import 'package:flutter/material.dart';

import '../models/contact.dart';
import '../services/api_client.dart';
import '../services/location_service.dart';
import '../services/ongoing_notice.dart';
import '../services/storage_service.dart';
import '../theme/app_theme.dart';
import '../widgets/step_scaffold.dart';
import 'home_shell.dart';

/// Screen 1 — Onboarding + contacts. One-time, first install only.
/// Four internal steps (intro, location permission, network authorisation,
/// contacts) presented as one continuous flow — still "one screen" for the
/// four-screen budget, since it's a single route the user never returns to.
class OnboardingScreen extends StatefulWidget {
  final StorageService storage;
  final LocationService location;

  /// Passed straight through to HomeShell on completion.
  ///
  /// Without these, a freshly-onboarded install landed on a HomeShell with
  /// a null theme callback and the daylight toggle silently did nothing
  /// until the app was restarted — i.e. it was broken on the only path a
  /// real user takes, and working on the one used to test it.
  final ThemeMode themeMode;
  final ValueChanged<ThemeMode>? onThemeModeChanged;

  const OnboardingScreen({
    super.key,
    required this.storage,
    required this.location,
    this.themeMode = ThemeMode.dark,
    this.onThemeModeChanged,
  });

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _pageController = PageController();
  static const _stepCount = 5;

  final _notice = OngoingNotice();

  bool _locationGranted = false;
  bool _networkAuthorised = false;
  bool _requestingLocation = false;

  /// Set once a permission request has come back refused.
  ///
  /// Step 2's primary button was the only action on the step and it only
  /// advanced on a grant — so once the OS marks location permanently denied
  /// (it stops showing the prompt at all and the request returns false
  /// immediately), the button visibly did nothing on every press and steps
  /// 3-5, registration included, became unreachable. Setup could not be
  /// completed at all, with no way out but reinstalling. This flag is what
  /// puts the settings route on screen.
  bool _locationDenied = false;

  // Fixed, matching scripts/run_demo.py's Config.msisdn default — the
  // conductor resets the whole travellers table at the start of every
  // scenario (Conductor.arm() -> backend.reset()), so this number is free
  // again each time regardless of who held it last. Sharing the identity
  // means a scenario's crossing shows up live in this app, not just on
  // the dashboard. If the backend gets reset out from under an
  // already-onboarded install, HomeShell's 401 handling sends the user
  // back here automatically — just submit again.
  final _nameController = TextEditingController(text: 'Sultan');
  final _msisdnController = TextEditingController(text: '+962790000001');
  final List<TextEditingController> _contactNameCtrls = [
    TextEditingController(text: 'Omar'),
  ];
  final List<TextEditingController> _contactMsisdnCtrls = [
    TextEditingController(text: '+962790000002'),
  ];
  bool _submitting = false;
  String? _error;

  // Test-alert step state.
  bool _testSending = false;
  Map<String, dynamic>? _testResult;
  String? _testError;

  void _goTo(int step) {
    _pageController.animateToPage(
      step,
      duration: const Duration(milliseconds: 260),
      curve: Curves.easeOut,
    );
  }

  Future<void> _requestLocation() async {
    setState(() => _requestingLocation = true);
    final granted = await widget.location.requestPermission();
    setState(() {
      _locationGranted = granted;
      _locationDenied = !granted;
      _requestingLocation = false;
    });
    if (granted) _goTo(2);
  }

  void _addContactField() {
    if (_contactNameCtrls.length >= 2) return;
    setState(() {
      _contactNameCtrls.add(TextEditingController());
      _contactMsisdnCtrls.add(TextEditingController());
    });
  }

  Future<void> _finish() async {
    final contacts = <EmergencyContact>[];
    for (var i = 0; i < _contactNameCtrls.length; i++) {
      final name = _contactNameCtrls[i].text.trim();
      final msisdn = _contactMsisdnCtrls[i].text.trim();
      if (name.isNotEmpty && msisdn.isNotEmpty) {
        contacts.add(EmergencyContact(name: name, msisdn: msisdn));
      }
    }
    if (contacts.isEmpty) {
      setState(() => _error = 'Add at least one emergency contact.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });

    final api = ApiClient(widget.storage);
    try {
      await widget.storage.setMsisdn(_msisdnController.text.trim());
      await widget.storage.setTravellerName(_nameController.text.trim());
      await widget.storage.setContacts(contacts);

      await api.registerTraveller(
        msisdn: _msisdnController.text.trim(),
        name: _nameController.text.trim(),
        contacts: contacts,
      );
      final zones = await api.fetchZones();
      if (zones.isNotEmpty) {
        await widget.storage.setZone(zones.first);
      }
      await widget.storage.setOnboarded(true);

      if (!mounted) return;
      // Registration succeeded — go to the proof step rather than straight
      // to the home screen. See _testStep for why that step exists.
      _goTo(4);
    } on ApiException catch (e) {
      // The backend was reached fine and rejected the request cleanly
      // (e.g. this phone number is already registered) — say that, not
      // "could not reach the backend", which is a different failure with
      // a different fix (check the Backend URL, not the phone number).
      setState(() => _error = e.message);
    } catch (e) {
      setState(() => _error = 'Could not reach the backend: $e');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: PageView(
        controller: _pageController,
        physics: const NeverScrollableScrollPhysics(),
        children: [
          _introStep(),
          _locationStep(),
          _networkAuthStep(),
          _contactsStep(),
          _testStep(),
        ],
      ),
    );
  }

  Future<void> _sendTest() async {
    setState(() {
      _testSending = true;
      _testError = null;
    });
    try {
      final result = await ApiClient(widget.storage).sendTestAlert();
      if (mounted) setState(() => _testResult = result);
    } catch (e) {
      if (mounted) setState(() => _testError = '$e');
    } finally {
      if (mounted) setState(() => _testSending = false);
    }
  }

  Future<void> _enterApp() async {
    // Ask for the notification grant here, with the other permissions,
    // rather than at the start of a crossing — which is while driving.
    await _notice.init();
    await _notice.requestPermission();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(
        builder: (_) => HomeShell(
          storage: widget.storage,
          location: widget.location,
          themeMode: widget.themeMode,
          onThemeModeChanged: widget.onThemeModeChanged,
        ),
      ),
    );
  }

  /// Step 5 — prove the alert actually reaches a human.
  ///
  /// Everything before this point is unverified assumption. The traveller
  /// typed a phone number, and nothing in the system ever checks it: if a
  /// digit is wrong, every tier still fires, every record is still
  /// written, and the message goes nowhere. Nobody finds out until the one
  /// moment it matters.
  ///
  /// It also fixes the other half of the problem — that setup ends with a
  /// person being told, correctly, to close the app and never open it
  /// again, having seen no evidence any of it works.
  ///
  /// The result is reported honestly, including the "no provider
  /// configured" case. A green tick over a message nobody received would
  /// be worse than no test at all: it converts an unknown into a false
  /// certainty.
  Widget _testStep() {
    final c = AppPalette.of(context);
    final result = _testResult;
    final delivery = result?['delivery'] as String?;

    return StepScaffold(
      stepIndex: 4,
      stepCount: _stepCount,
      title: 'Check it reaches them',
      subtitle:
          'One test message, so you know the number works. Nothing else in '
          'SignalGuard ever checks it — and a wrong digit means an alert '
          'that goes nowhere on the day it matters.',
      body: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (result == null && _testError == null)
              Center(
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 20),
                  child: Icon(
                    Icons.forward_to_inbox_rounded,
                    size: 80,
                    color: c.textMuted,
                  ),
                ),
              ),
            if (_testError != null)
              _TestBanner(
                icon: Icons.error_outline_rounded,
                tone: c.danger,
                title: "Couldn't send the test",
                body: _testError!,
              ),
            if (delivery == 'sent')
              _TestBanner(
                icon: Icons.mark_email_read_rounded,
                tone: c.accent,
                title: 'Sent',
                body: 'Ask them to confirm it arrived. If it did not, go '
                    'back and check the number.',
              ),
            if (delivery == 'not_configured')
              _TestBanner(
                icon: Icons.info_outline_rounded,
                tone: c.amber,
                title: 'No messaging provider connected',
                body: 'The message was composed and addressed correctly, but '
                    'this build has no SMS or WhatsApp provider wired up, so '
                    'nothing was actually delivered. Your contact is saved — '
                    'the number itself is still unverified.',
              ),
            if (delivery == 'no_contacts')
              _TestBanner(
                icon: Icons.person_off_outlined,
                tone: c.danger,
                title: 'No contacts saved',
                body: 'Go back and add at least one emergency contact.',
              ),
            if (result != null) ...[
              const SizedBox(height: 14),
              for (final entry in (result['contacts'] as List? ?? []))
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(
                    children: [
                      Icon(
                        (entry['sent'] as bool? ?? false)
                            ? Icons.check_circle_outline_rounded
                            : Icons.radio_button_unchecked_rounded,
                        size: 16,
                        color: (entry['sent'] as bool? ?? false)
                            ? c.accent
                            : c.textMuted,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          '${entry['name']} · ${entry['msisdn']}',
                          style: TextStyle(color: c.textSecondary, fontSize: 13),
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ],
        ),
      ),
      primaryLabel: _testSending
          ? 'Sending…'
          : (result == null ? 'Send a test message' : 'Finish setup'),
      onPrimary: _testSending
          ? null
          : (result == null ? _sendTest : _enterApp),
      primaryEnabled: !_testSending,
      // Skipping is allowed — this is a check, not a gate, and a traveller
      // whose contact is asleep should not be blocked from finishing.
      secondary: result == null && !_testSending
          ? TextButton(
              onPressed: _enterApp,
              child: const Text('Skip — I\'ll check later'),
            )
          : null,
    );
  }

  Widget _introStep() {
    return StepScaffold(
      stepIndex: 0,
      stepCount: _stepCount,
      title: 'SignalGuard watches\nthe roads GPS can’t.',
      subtitle:
          'We monitor your trips through low-coverage zones and alert '
          'your contacts if you don’t come out on time. Set up once, '
          'then never open the app again.',
      body: Center(
        child: Icon(
          Icons.podcasts_rounded,
          size: 96,
          color: Theme.of(context).colorScheme.primary.withValues(alpha: 0.85),
        ),
      ),
      primaryLabel: 'Get started',
      onPrimary: () => _goTo(1),
    );
  }

  Widget _locationStep() {
    return StepScaffold(
      stepIndex: 1,
      stepCount: _stepCount,
      title: 'Background location',
      // The denied copy is written as a condition, not an assertion: the
      // request returning false covers both an ordinary decline (where the
      // prompt did appear) and a permanently-denied permission (where it
      // never will again), and this screen cannot tell those apart. So it
      // describes the symptom the traveller can check for themselves and
      // gives them the route out either way.
      subtitle: _locationDenied && !_locationGranted
          ? 'Location is still off. The system only offers that prompt once '
              'or twice — if pressing Allow does nothing now, it has stopped '
              'asking. Turn Location on for SignalGuard in device settings, '
              'then come back and check again.'
          : 'Used only inside a monitored dead zone, to show your position '
              'on the offline map and as a local backup if the network signal '
              'is delayed. Detection itself runs on the network, not your GPS.',
      // Center, not a bare Column — StepScaffold's outer Column is
      // left-aligned (for the title/subtitle text), and a plain Column
      // shrink-wraps to its widest child under that alignment rather than
      // filling the row, which pins a lone icon to the left edge instead
      // of centering it. Center always fills the available bounded width
      // regardless, so it doesn't inherit that problem.
      body: Center(
        child: Icon(
          _locationGranted
              ? Icons.check_circle_rounded
              : Icons.location_on_outlined,
          size: 88,
          color: _locationGranted
              ? AppPalette.of(context).accent
              : AppPalette.of(context).textMuted,
        ),
      ),
      // "Check again" rather than "Allow all the time" once refused: after
      // a trip to the settings page, re-requesting is exactly what the
      // traveller wants this button to do — it returns granted immediately
      // and the flow moves on — but labelling it "Allow" makes it look
      // like the same dead press that got them here.
      primaryLabel: _locationGranted
          ? 'Continue'
          : (_requestingLocation
              ? 'Requesting…'
              : (_locationDenied ? 'Check again' : 'Allow all the time')),
      onPrimary: _locationGranted ? () => _goTo(2) : _requestLocation,
      primaryEnabled: !_requestingLocation,
      // No "continue without location" here on purpose — that would change
      // what this app is allowed to do, not just how setup flows. The
      // settings page is the way out.
      secondary: _locationDenied && !_locationGranted
          ? TextButton(
              onPressed: widget.location.openSettings,
              child: const Text('Open device settings'),
            )
          : null,
      onBack: _requestingLocation ? null : () => _goTo(0),
    );
  }

  Widget _networkAuthStep() {
    // Prototype-phase stand-in for a real OIDC/CIBA authorisation flow
    // against the operator — out of scope per the build prompt. This is
    // the load-bearing switch (see Security & Privacy doc §3.3): revoking
    // it, not the OS location permission, is what actually stops
    // monitoring.
    return StepScaffold(
      stepIndex: 2,
      stepCount: _stepCount,
      title: 'Authorise network access',
      subtitle:
          'Your operator confirms who you are and enables network-side '
          'detection for your number. This is separate from the location '
          'permission above, and you can revoke it at any time through '
          'your carrier — independently of this app.',
      // Center wrapper — same reason as _locationStep above.
      body: Center(
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 300),
          child: _networkAuthorised
              ? Column(
                  key: const ValueKey('done'),
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.verified_rounded,
                      size: 88,
                      color: AppPalette.of(context).accent,
                    ),
                    const SizedBox(height: 16),
                    const Text('Authorised at your operator'),
                  ],
                )
              : Icon(
                  Icons.cell_tower_rounded,
                  key: const ValueKey('pending'),
                  size: 88,
                  color: AppPalette.of(context).textMuted,
                ),
        ),
      ),
      primaryLabel: _networkAuthorised ? 'Continue' : 'Authorise',
      onPrimary: _networkAuthorised
          ? () => _goTo(3)
          : () => setState(() => _networkAuthorised = true),
      onBack: () => _goTo(1),
    );
  }

  Widget _contactsStep() {
    return StepScaffold(
      stepIndex: 3,
      stepCount: _stepCount,
      title: 'Emergency contacts',
      subtitle:
          'Texted only if a crossing goes overdue — never otherwise.',
      body: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _nameController,
              decoration: const InputDecoration(labelText: 'Your name'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _msisdnController,
              keyboardType: TextInputType.phone,
              decoration: const InputDecoration(labelText: 'Your phone number'),
            ),
            const SizedBox(height: 24),
            const Divider(),
            const SizedBox(height: 16),
            for (var i = 0; i < _contactNameCtrls.length; i++) ...[
              TextField(
                controller: _contactNameCtrls[i],
                decoration: InputDecoration(
                  labelText: 'Contact ${i + 1} name',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _contactMsisdnCtrls[i],
                keyboardType: TextInputType.phone,
                decoration: InputDecoration(
                  labelText: 'Contact ${i + 1} phone number',
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (_contactNameCtrls.length < 2)
              TextButton.icon(
                onPressed: _addContactField,
                icon: const Icon(Icons.add_rounded),
                label: const Text('Add a second contact'),
              ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Semantics(
                liveRegion: true,
                child: Text(
                _error!,
                style: TextStyle(
                  color: AppPalette.of(context).danger,
                  fontSize: 13,
                ),
              )),
            ],
          ],
        ),
      ),
      primaryLabel: _submitting ? 'Setting up…' : 'Next',
      onPrimary: _finish,
      primaryEnabled: !_submitting,
      onBack: _submitting ? null : () => _goTo(2),
    );
  }
}


class _TestBanner extends StatelessWidget {
  final IconData icon;
  final Color tone;
  final String title;
  final String body;

  const _TestBanner({
    required this.icon,
    required this.tone,
    required this.title,
    required this.body,
  });

  @override
  Widget build(BuildContext context) {
    final c = AppPalette.of(context);
    return Semantics(
      liveRegion: true,
      label: '$title. $body',
      excludeSemantics: true,
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: tone.withValues(alpha: 0.1),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: tone.withValues(alpha: 0.35)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: tone, size: 18),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: TextStyle(
                      color: tone,
                      fontWeight: FontWeight.w600,
                      fontSize: 13,
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    body,
                    style: TextStyle(
                      color: c.textSecondary,
                      fontSize: 12,
                      height: 1.45,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

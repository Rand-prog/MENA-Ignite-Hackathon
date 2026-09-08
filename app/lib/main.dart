import 'package:flutter/material.dart';

import 'screens/home_shell.dart';
import 'screens/onboarding_screen.dart';
import 'services/location_service.dart';
import 'services/storage_service.dart';
import 'theme/app_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const SignalGuardApp());
}

class SignalGuardApp extends StatefulWidget {
  const SignalGuardApp({super.key});

  @override
  State<SignalGuardApp> createState() => _SignalGuardAppState();
}

class _SignalGuardAppState extends State<SignalGuardApp> {
  final _location = LocationService();
  StorageService? _storage;

  /// Night by default — the app is used in a car, often after dark.
  ///
  /// But dark-only was a real usability failure in the one environment this
  /// product is guaranteed to be used in: a desert highway at midday, phone
  /// in a windshield mount, where a near-black screen is unreadable. The
  /// choice is persisted because a traveller who switched to daylight once
  /// is going to want it every time they drive that road.
  ThemeMode _themeMode = ThemeMode.dark;

  @override
  void initState() {
    super.initState();
    StorageService.create().then((s) {
      if (mounted) {
        setState(() {
          _storage = s;
          _themeMode = s.themeMode;
        });
      }
    });
  }

  @override
  void dispose() {
    _location.dispose();
    super.dispose();
  }

  void _setThemeMode(ThemeMode mode) {
    setState(() => _themeMode = mode);
    _storage?.setThemeMode(mode);
  }

  @override
  Widget build(BuildContext context) {
    final storage = _storage;
    return MaterialApp(
      title: 'SignalGuard',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(brightness: Brightness.light),
      darkTheme: buildAppTheme(brightness: Brightness.dark),
      themeMode: _themeMode,
      home: storage == null
          ? const _SplashScreen()
          : (storage.onboarded
                ? HomeShell(
                    storage: storage,
                    location: _location,
                    themeMode: _themeMode,
                    onThemeModeChanged: _setThemeMode,
                  )
                : OnboardingScreen(
                    storage: storage,
                    location: _location,
                    themeMode: _themeMode,
                    onThemeModeChanged: _setThemeMode,
                  )),
    );
  }
}

class _SplashScreen extends StatelessWidget {
  const _SplashScreen();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: CircularProgressIndicator(
          color: Theme.of(context).colorScheme.primary,
        ),
      ),
    );
  }
}

import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/contact.dart';
import '../models/zone.dart';

/// Everything the app persists locally: onboarding state, the traveller's
/// own auth token, cached zone geometry. No trip state lives here — that's
/// the backend's job entirely (see Trip model's doc comment). This is
/// purely "have I already onboarded" and "what do I authenticate as."
class StorageService {
  static const _kOnboarded = 'onboarded';
  static const _kAuthToken = 'auth_token';
  static const _kTravellerId = 'traveller_id';
  static const _kTravellerName = 'traveller_name';
  static const _kMsisdn = 'msisdn';
  static const _kContacts = 'contacts';
  static const _kZone = 'zone';
  static const _kBackendUrl = 'backend_url';
  static const _kThemeMode = 'theme_mode';

  final SharedPreferences _prefs;
  StorageService(this._prefs);

  static Future<StorageService> create() async =>
      StorageService(await SharedPreferences.getInstance());

  bool get onboarded => _prefs.getBool(_kOnboarded) ?? false;
  Future<void> setOnboarded(bool v) => _prefs.setBool(_kOnboarded, v);

  String? get authToken => _prefs.getString(_kAuthToken);
  Future<void> setAuthToken(String v) => _prefs.setString(_kAuthToken, v);

  String? get travellerId => _prefs.getString(_kTravellerId);
  Future<void> setTravellerId(String v) => _prefs.setString(_kTravellerId, v);

  String? get travellerName => _prefs.getString(_kTravellerName);
  Future<void> setTravellerName(String v) =>
      _prefs.setString(_kTravellerName, v);

  String? get msisdn => _prefs.getString(_kMsisdn);
  Future<void> setMsisdn(String v) => _prefs.setString(_kMsisdn, v);

  List<EmergencyContact> get contacts {
    final raw = _prefs.getString(_kContacts);
    if (raw == null) return const [];
    final list = jsonDecode(raw) as List;
    return list
        .map((e) => EmergencyContact.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  Future<void> setContacts(List<EmergencyContact> contacts) =>
      _prefs.setString(
        _kContacts,
        jsonEncode(contacts.map((c) => c.toJson()).toList()),
      );

  Zone? get zone {
    final raw = _prefs.getString(_kZone);
    if (raw == null) return null;
    return Zone.fromJson(jsonDecode(raw) as Map<String, dynamic>);
  }

  Future<void> setZone(Zone zone) =>
      _prefs.setString(_kZone, jsonEncode(zone.toJson()));

  // Backend base URL is user-configurable at onboarding so the app can
  // point at a laptop's LAN IP during the hackathon demo (an Android
  // emulator/device can't reach a bare "localhost" meaning the backend
  // host).
  String get backendUrl =>
      _prefs.getString(_kBackendUrl) ?? 'http://10.0.2.2:8000';
  Future<void> setBackendUrl(String v) => _prefs.setString(_kBackendUrl, v);

  /// Night / daylight / follow-the-system.
  ///
  /// Defaults to night: the app is used in a car and often after dark. But
  /// a traveller who switched to daylight once — because a near-black
  /// screen is unreadable on a desert highway at noon — wants that choice
  /// to stick, so it is persisted rather than being a per-session toggle.
  ThemeMode get themeMode {
    switch (_prefs.getString(_kThemeMode)) {
      case 'light':
        return ThemeMode.light;
      case 'system':
        return ThemeMode.system;
      default:
        return ThemeMode.dark;
    }
  }

  Future<void> setThemeMode(ThemeMode mode) => _prefs.setString(
        _kThemeMode,
        switch (mode) {
          ThemeMode.light => 'light',
          ThemeMode.system => 'system',
          ThemeMode.dark => 'dark',
        },
      );
}

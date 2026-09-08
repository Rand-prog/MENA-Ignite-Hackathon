import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/contact.dart';
import '../models/trip.dart';
import '../models/zone.dart';
import 'storage_service.dart';

class ApiException implements Exception {
  final String message;
  final int? statusCode;
  ApiException(this.message, {this.statusCode});
  bool get isUnauthorized => statusCode == 401;
  @override
  String toString() => message;
}

/// Talks to the real product surface only — /travellers, /zones,
/// /travellers/me/*. Never touches /demo/* (that's the conductor's job).
/// See backend/app/routers/real.py.
class ApiClient {
  final StorageService storage;
  final http.Client _http;

  ApiClient(this.storage, {http.Client? client})
    : _http = client ?? http.Client();

  Uri _uri(String path) => Uri.parse('${storage.backendUrl}$path');

  Map<String, String> get _authHeaders {
    final token = storage.authToken;
    return {
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
    };
  }

  Future<List<Zone>> fetchZones() async {
    final resp = await _http
        .get(_uri('/zones'), headers: _authHeaders)
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      throw ApiException('failed to load zones (${resp.statusCode})');
    }
    final list = jsonDecode(resp.body) as List;
    return list.map((e) => Zone.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<void> registerTraveller({
    required String msisdn,
    required String name,
    required List<EmergencyContact> contacts,
  }) async {
    final resp = await _http
        .post(
          _uri('/travellers'),
          headers: _authHeaders,
          body: jsonEncode({
            'msisdn': msisdn,
            'name': name,
            'contacts': contacts.map((c) => c.toJson()).toList(),
          }),
        )
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      String detail = '';
      try {
        detail = (jsonDecode(resp.body) as Map<String, dynamic>)['detail'] as String? ?? '';
      } catch (_) {
        // non-JSON error body — fall through with an empty detail
      }
      throw ApiException(
        detail.isNotEmpty ? detail : 'registration failed (${resp.statusCode})',
      );
    }
    final body = jsonDecode(resp.body) as Map<String, dynamic>;
    await storage.setTravellerId(body['traveller_id'] as String);
    await storage.setAuthToken(body['auth_token'] as String);
  }

  /// Battery telemetry, in the request body.
  ///
  /// This used to go in the query string as `?level=42`. A URL is the most
  /// widely logged part of an HTTP request — proxies, gateways and access
  /// logs keep it by default — so a person's device telemetry ended up
  /// copied into several places nobody audits, for no benefit.
  Future<void> reportBattery(int level) async {
    try {
      await _http
          .post(
            _uri('/travellers/me/battery'),
            headers: _authHeaders,
            body: jsonEncode({'level': level}),
          )
          .timeout(const Duration(seconds: 8));
    } catch (_) {
      // Battery reporting is best-effort — a dropped report while inside a
      // dead zone is expected, not an error. The agent already read the
      // last-known level before signal was lost.
    }
  }

  /// "I'm stopping for a while" — declared before setting off, or during a
  /// brief reconnection mid-crossing.
  ///
  /// The commonest reason a crossing runs past its window is not an
  /// emergency, it is a person who stopped. Every one of those wakes a
  /// contact about somebody sitting in a roadside cafe, and the real cost
  /// is not the one message — it is that after a few of them the contact
  /// stops taking the messages seriously.
  Future<void> declarePlannedStop(int minutes) async {
    final resp = await _http
        .post(
          _uri('/travellers/me/planned-stop'),
          headers: _authHeaders,
          body: jsonEncode({'minutes': minutes}),
        )
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      throw ApiException(
        'could not extend your window (${resp.statusCode})',
        statusCode: resp.statusCode,
      );
    }
  }

  /// Send the "this is what an alert looks like" message from onboarding.
  ///
  /// Returns the backend's honest per-contact delivery report — including
  /// `delivery: "not_configured"` when no SMS/WhatsApp provider is wired
  /// up. The caller must show that state rather than a green tick: a
  /// setup screen that claims success for a message nobody received is
  /// worse than no test at all, because it converts an unknown into a
  /// false certainty.
  Future<Map<String, dynamic>> sendTestAlert() async {
    final resp = await _http
        .post(_uri('/travellers/me/test-alert'), headers: _authHeaders)
        .timeout(const Duration(seconds: 15));
    if (resp.statusCode >= 400) {
      throw ApiException(
        'could not send the test message (${resp.statusCode})',
        statusCode: resp.statusCode,
      );
    }
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  /// Answer the Tier 0 ping. "safe" closes the trip with no human ever
  /// contacted — see backend/app/state_machine.py's tier0_answer.
  Future<void> answerTier0(String answer) async {
    final resp = await _http
        .post(
          _uri('/travellers/me/tier0-response'),
          headers: _authHeaders,
          body: jsonEncode({'answer': answer}),
        )
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      throw ApiException(
        'could not send your reply (${resp.statusCode})',
        statusCode: resp.statusCode,
      );
    }
  }

  /// Returns null when there is no active trip (the common case).
  Future<Trip?> fetchCurrentTrip() async {
    final resp = await _http
        .get(_uri('/travellers/me/trip'), headers: _authHeaders)
        .timeout(const Duration(seconds: 10));
    if (resp.statusCode >= 400) {
      throw ApiException(
        'failed to load trip (${resp.statusCode})',
        statusCode: resp.statusCode,
      );
    }
    if (resp.body.trim() == 'null' || resp.body.trim().isEmpty) return null;
    return Trip.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }
}
